"""Compare supplied normalized Study snapshots without accessing application state.

Version 1 requires exactly schema_version (integer 1), workspace_id (nonempty
string), unresolved (empty list), and entities (all eight ENTITY_GROUPS). Each
group is a list of objects containing exactly key (nonempty string) and data
(JSON object). Identities must already be approved; this tool never infers them.

Run: python database/tooling/study_snapshot_compare.py BEFORE AFTER
Exit codes: 0 equal, 1 different, 2 invalid. Missing means a before key is absent
after; extra means an after key is absent before. Changed paths are JSON Pointers
relative to a record's data, with array indexes and escaped object member names.
Output contains keys and paths, never data values or workspace identifiers.

Equality certifies only the supplied normalized documents, not extractor
completeness, historical compatibility, migration safety, or cutover readiness.
"""

import argparse
import json
import math
import sys


ENTITY_GROUPS = (
    "cards",
    "reviews",
    "schedules",
    "introductions",
    "accepted_definitions",
    "queue_attempts",
    "operation_receipts",
    "browser_intents",
)


class SnapshotError(ValueError):
    """A supplied document cannot be compared under the version 1 contract."""


def _validate_json(value):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _validate_json(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _validate_json(item)
        return
    raise SnapshotError("data must contain only JSON values with finite numbers")


def _index_snapshot(snapshot, label):
    required_fields = {"schema_version", "workspace_id", "unresolved", "entities"}
    if type(snapshot) is not dict or set(snapshot) != required_fields:
        raise SnapshotError(f"{label}: expected exactly the version 1 fields")
    if type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != 1:
        raise SnapshotError(f"{label}: schema_version must be integer 1")
    workspace_id = snapshot["workspace_id"]
    if type(workspace_id) is not str or not workspace_id:
        raise SnapshotError(f"{label}: workspace_id must be a nonempty string")
    if type(snapshot["unresolved"]) is not list or snapshot["unresolved"]:
        raise SnapshotError(f"{label}: unresolved must be an empty list")
    entities = snapshot["entities"]
    if type(entities) is not dict or set(entities) != set(ENTITY_GROUPS):
        raise SnapshotError(f"{label}: entities must contain exactly all eight groups")

    indexed = {}
    for group in ENTITY_GROUPS:
        rows = entities[group]
        if type(rows) is not list:
            raise SnapshotError(f"{label}: {group} must be a list")
        records = {}
        for record in rows:
            if type(record) is not dict or set(record) != {"key", "data"}:
                raise SnapshotError(f"{label}: {group} records require exactly key and data")
            key = record["key"]
            if type(key) is not str or not key:
                raise SnapshotError(f"{label}: {group} record keys must be nonempty strings")
            if key in records:
                raise SnapshotError(f"{label}: {group} contains a duplicate record key")
            if type(record["data"]) is not dict:
                raise SnapshotError(f"{label}: {group} record data must be a JSON object")
            _validate_json(record["data"])
            records[key] = record["data"]
        indexed[group] = records
    return workspace_id, indexed


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _pointer_child(path, member):
    escaped = str(member).replace("~", "~0").replace("/", "~1")
    return f"{path}/{escaped}"


def _changed_paths(before, after, path=""):
    paths = []
    if type(before) is dict and type(after) is dict:
        for member in sorted(before.keys() | after.keys()):
            child = _pointer_child(path, member)
            if member not in before or member not in after:
                paths.append(child)
            else:
                paths.extend(_changed_paths(before[member], after[member], child))
    elif type(before) is list and type(after) is list:
        for index in range(max(len(before), len(after))):
            child = _pointer_child(path, index)
            if index >= len(before) or index >= len(after):
                paths.append(child)
            else:
                paths.extend(_changed_paths(before[index], after[index], child))
    elif _canonical(before) != _canonical(after):
        paths.append(path)
    return paths


def compare_snapshots(before: object, after: object) -> dict[str, object]:
    """Validate two JSON documents and return deterministic key/path differences.

    Neither input is mutated. Every group is present in the result, including
    equal groups. Each group's changed list contains {key, paths} objects.
    """
    before_workspace, before_entities = _index_snapshot(before, "before")
    after_workspace, after_entities = _index_snapshot(after, "after")
    if before_workspace != after_workspace:
        raise SnapshotError("workspace IDs must match")

    equal = True
    differences = {}
    for group in ENTITY_GROUPS:
        before_records = before_entities[group]
        after_records = after_entities[group]
        missing = sorted(before_records.keys() - after_records.keys())
        extra = sorted(after_records.keys() - before_records.keys())
        changed = []
        for key in sorted(before_records.keys() & after_records.keys()):
            before_data = before_records[key]
            after_data = after_records[key]
            if _canonical(before_data) != _canonical(after_data):
                changed.append({
                    "key": key,
                    "paths": sorted(_changed_paths(before_data, after_data)),
                })
        differences[group] = {"missing": missing, "extra": extra, "changed": changed}
        if missing or extra or changed:
            equal = False
    return {"equal": equal, "entities": differences}


def _object_without_duplicates(pairs):
    result = {}
    for member, value in pairs:
        if member in result:
            raise SnapshotError("JSON objects must not repeat member names")
        result[member] = value
    return result


def _reject_constant(_value):
    raise SnapshotError("JSON numbers must be finite")


def _load_snapshot(path):
    try:
        with open(path, encoding="utf-8") as source:
            return json.load(
                source,
                object_pairs_hook=_object_without_duplicates,
                parse_constant=_reject_constant,
            )
    except (OSError, UnicodeError, ValueError, RecursionError):
        # Do not expose file names, source snippets, or payload values in errors.
        raise SnapshotError("input must be readable UTF-8 JSON with unique object members") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", metavar="BEFORE")
    parser.add_argument("after", metavar="AFTER")
    arguments = parser.parse_args(argv)
    try:
        result = compare_snapshots(
            _load_snapshot(arguments.before), _load_snapshot(arguments.after)
        )
    except (SnapshotError, RecursionError) as error:
        message = (
            "snapshot is too deeply nested"
            if isinstance(error, RecursionError) else str(error)
        )
        print(json.dumps({"error": message}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["equal"] else 1


if __name__ == "__main__":
    sys.exit(main())
