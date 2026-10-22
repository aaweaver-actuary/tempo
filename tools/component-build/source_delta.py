#!/usr/bin/env python3
"""Report source-line deltas for paths in a reviewer-supplied manifest.

Physical lines are counted as LF bytes plus one when a nonempty file's last
byte is not LF. A path's category is never inferred from its name. Totals are
scoped to the explicit classification manifest, not the whole repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


CATEGORIES = {
    "narrow-production",
    "broad-production",
    "test",
    "docs",
    "other",
}
PROTECTED_ROOTS = (
    "backend",
    "database",
    "services",
    "packages",
    "workers",
    "contracts",
)


class SourceDeltaError(Exception):
    """An invalid input or unavailable Git object prevented a valid report."""


def _git(repo: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise SourceDeltaError(f"cannot run git: {exc}") from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise SourceDeltaError(detail or f"git {' '.join(args[:2])} failed")
    return result.stdout


def _repo_root() -> Path:
    try:
        raw = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise SourceDeltaError(f"cannot run git: {exc}") from exc
    if raw.returncode:
        raise SourceDeltaError("current directory is not inside a Git repository")
    return Path(raw.stdout.decode("utf-8", "strict").strip())


def _resolve_commit(repo: Path, revision: str, label: str) -> str:
    try:
        value = _git(repo, "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}")
    except SourceDeltaError as exc:
        raise SourceDeltaError(f"invalid or missing {label} commit {revision!r}: {exc}") from exc
    return value.decode("ascii").strip()


def _read_classification(path: str) -> tuple[dict[str, str], str]:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise SourceDeltaError(f"cannot read classification file {path!r}: {exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceDeltaError(f"classification file is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(document, dict) or set(document) != {"schema_version", "paths"}:
        raise SourceDeltaError("classification must contain exactly schema_version and paths")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise SourceDeltaError("classification schema_version must be integer 1")
    if not isinstance(document["paths"], list):
        raise SourceDeltaError("classification paths must be a list")
    classifications: dict[str, str] = {}
    for index, entry in enumerate(document["paths"]):
        if not isinstance(entry, dict) or set(entry) != {"path", "category"}:
            raise SourceDeltaError(f"paths[{index}] must contain exactly path and category")
        name, category = entry["path"], entry["category"]
        if not isinstance(name, str) or not name or name.startswith("/") or "\x00" in name:
            raise SourceDeltaError(f"paths[{index}].path must be a nonempty repository-relative path")
        if any(part in ("", ".", "..") for part in name.split("/")):
            raise SourceDeltaError(f"paths[{index}].path is not a normalized repository-relative path")
        if not isinstance(category, str) or category not in CATEGORIES:
            raise SourceDeltaError(f"paths[{index}].category is unknown or invalid")
        if name in classifications:
            raise SourceDeltaError(f"duplicate classification path: {name!r}")
        classifications[name] = category
    return classifications, digest


def _decode_git_path(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceDeltaError("Git contains a path that is not valid UTF-8") from exc


def _tree(repo: Path, commit: str) -> dict[str, str]:
    raw = _git(repo, "ls-tree", "-r", "-z", "--full-tree", commit)
    objects: dict[str, str] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path = record.split(b"\t", 1)
            mode, object_type, object_id = metadata.split(b" ", 2)
        except ValueError as exc:
            raise SourceDeltaError("could not parse NUL-delimited Git tree output") from exc
        if object_type == b"blob" and mode != b"160000":
            objects[_decode_git_path(path)] = object_id.decode("ascii")
    return objects


def _changed_paths(repo: Path, base: str, head: str) -> list[dict[str, str]]:
    raw = _git(repo, "diff", "--name-status", "-z", "--find-renames", "--no-ext-diff", base, head, "--")
    fields = raw.split(b"\0")
    rows: list[dict[str, str]] = []
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index].decode("ascii")
        index += 1
        if status.startswith(("R", "C")):
            if index + 1 >= len(fields):
                raise SourceDeltaError("could not parse rename/copy paths from Git diff")
            old_path = _decode_git_path(fields[index])
            new_path = _decode_git_path(fields[index + 1])
            index += 2
            if status.startswith("R"):
                rows.append({"status": status, "path": old_path, "role": "old"})
                rows.append({"status": status, "path": new_path, "role": "new"})
            else:
                rows.append({"status": status, "path": old_path, "role": "source"})
                rows.append({"status": status, "path": new_path, "role": "copy"})
        else:
            if index >= len(fields):
                raise SourceDeltaError("could not parse path from Git diff")
            rows.append({"status": status, "path": _decode_git_path(fields[index]), "role": "path"})
            index += 1
    return sorted(rows, key=lambda row: (row["path"], row["status"], row["role"]))


def _is_protected(path: str) -> bool:
    return any(path == root or path.startswith(root + "/") for root in PROTECTED_ROOTS)


def _line_count(repo: Path, object_id: str | None, path: str, commit_label: str) -> int:
    if object_id is None:
        return 0
    try:
        content = _git(repo, "cat-file", "blob", object_id)
    except SourceDeltaError as exc:
        raise SourceDeltaError(f"cannot read {commit_label} blob for {path!r}: {exc}") from exc
    if b"\0" in content:
        raise SourceDeltaError(f"binary source cannot be counted: {path!r} at {commit_label}")
    if not content:
        return 0
    return content.count(b"\n") + (0 if content.endswith(b"\n") else 1)


def build_report(repo: Path, base_arg: str, head_arg: str, classification_file: str) -> dict[str, Any]:
    base = _resolve_commit(repo, base_arg, "base")
    head = _resolve_commit(repo, head_arg, "head")
    classifications, manifest_hash = _read_classification(classification_file)
    base_tree = _tree(repo, base)
    head_tree = _tree(repo, head)
    changes = _changed_paths(repo, base, head)

    changed_names = {row["path"] for row in changes}
    missing = sorted(name for name in changed_names if _is_protected(name) and name not in classifications)
    if missing:
        raise SourceDeltaError("missing classifications for changed protected paths: " + ", ".join(repr(x) for x in missing))
    absent = sorted(name for name in classifications if name not in base_tree and name not in head_tree)
    if absent:
        raise SourceDeltaError("classified paths are absent from both commits: " + ", ".join(repr(x) for x in absent))

    before_by_category = {category: 0 for category in CATEGORIES}
    after_by_category = {category: 0 for category in CATEGORIES}
    for name, category in classifications.items():
        before_by_category[category] += _line_count(repo, base_tree.get(name), name, "base")
        after_by_category[category] += _line_count(repo, head_tree.get(name), name, "head")

    changed_table = []
    for row in changes:
        name = row["path"]
        category = classifications.get(name)
        before = _line_count(repo, base_tree.get(name), name, "base") if category else None
        after = _line_count(repo, head_tree.get(name), name, "head") if category else None
        changed_table.append(
            {
                "path": name,
                "status": row["status"],
                "rename_role": row["role"] if row["role"] != "path" else None,
                "category": category,
                "before_lines": before,
                "after_lines": after,
                "net_lines": (after - before) if category else None,
            }
        )

    category_totals: dict[str, dict[str, int]] = {}
    for category in ("narrow-production", "broad-production", "test", "docs", "other"):
        before = before_by_category[category]
        after = after_by_category[category]
        if category == "broad-production":
            before += before_by_category["narrow-production"]
            after += after_by_category["narrow-production"]
        category_totals[category] = {"before": before, "after": after, "net": after - before}

    return {
        "scope": "explicitly classified paths only; not a whole-repository total or original baseline",
        "base_commit": base,
        "head_commit": head,
        "classification_sha256": manifest_hash,
        "line_count_rule": "LF bytes plus one if the nonempty file does not end in LF; empty files count as zero",
        "totals": category_totals,
        "changed_paths": changed_table,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="base commit SHA")
    parser.add_argument("--head", required=True, help="head commit SHA")
    parser.add_argument("--classification", required=True, help="frozen classification JSON file")
    args = parser.parse_args(argv)
    try:
        report = build_report(_repo_root(), args.base, args.head, args.classification)
    except SourceDeltaError as exc:
        print(f"source_delta: error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
