"""Verify optional JSON graph translation files and prepare existing import input.

A native 14-table pg_dump archive with its successful consistent-snapshot command,
independent copy hash and disposable pg_restore/usability proof is an equivalent
graph recovery path. This module's JSON capsule, receipt and SourceSnapshot pack
are optional downstream preparation, never prerequisites of native recovery.

This module never captures a database, imports private feature implementations,
or writes a target. The outer composition supplies graph-closure/consistent-read
inspection, the existing public Content authored-import validation, the fresh
target observer and its existing import planner. Historical reviews, schedules,
background receipts and browser state are outside this graph-only operation.

The recovery manifest is file evidence, not a new application graph schema.
``capture`` retains the complete 14-table raw capsule. The public graph-only
CLI receives the existing {tables, graphs} projection separately;
``source`` retains Content SourceSnapshot v1, separately from Learning's reduced
serving projection. Keep all four original artifacts after a fresh import.
"""

import copy
import hashlib
import json
import re


SOURCE_REVISION = "86d11afe8579ac56b2a39d8d63984180c7f3c194"
CLOSURE_SHA256 = "1c9b8b6b72037097c9ffa427e02210d239d38b799cd36da599eda7858d26ed3a"
ARTIFACT_NAMES = ("capture", "capture_receipt", "source", "import_manifest")
CAPTURE_PRIMARY_KEYS = {
    "repertoires": ("id",),
    "repertoire_lines": ("id",),
    "repertoire_cards": ("repertoire_id", "card_id"),
    "cards": ("id",),
    "opening_graph_publications": ("repertoire_id",),
    "opening_graph_steps": ("repertoire_id", "generation", "line_id", "decision_index"),
    "repertoire_line_training_depths": ("line_id",),
    "prefix_splits": ("source_card_id",),
    "settings": ("id",),
    "deleted_cards": ("card_id",),
    "canonical_prefix_previews": ("id",),
    "canonical_prefix_results": ("preview_id", "item_id"),
    "canonical_prefix_positions": ("preview_id", "fen_key", "in_scope"),
    "opening_graph_legacy_mappings": ("repertoire_id", "generation", "legacy_card_id", "decision_card_id"),
}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class GraphTransferError(ValueError):
    """The supplied graph copy or explicit preparation contract failed."""


def capture_query_plan():
    """Return fixed SQL DATA for the sole authorized executor; execute nothing.

    This is an optional SQL-to-JSON adapter plan, not a native pg_dump gate.
    Stream all rows/columns once; derive counts and column names from each same
    stream. No count rescans, table filters, guessed IDs, background queries,
    migration-ledger queries or database-wide discovery are included.
    """
    return {
        "source_commit": SOURCE_REVISION,
        "closure_sha256": CLOSURE_SHA256,
        "begin": "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
        "snapshot_query": "SELECT txid_current_snapshot()::text AS snapshot_id",
        "queries": [{"table": table, "sql": f"SELECT * FROM public.{table} ORDER BY {','.join(keys)}"}
                    for table, keys in CAPTURE_PRIMARY_KEYS.items()],
        "end": "ROLLBACK",
        "raw_capsule": {"top_level_exact": ["tables"], "tables_exact": list(CAPTURE_PRIMARY_KEYS)},
        "receipt_exact_fields": ["schema_version", "source_commit", "source_target_id",
                                 "selected_repertoire_ids", "capture_sha256", "closure_sha256",
                                 "consistent_read", "tables"],
        "receipt_table_fields": ["row_count", "columns"],
        "receipt_consistent_read": {"read_only": True, "isolation": "repeatable_read",
                                    "snapshot_id": "EXACT OBSERVED SNAPSHOT STRING",
                                    "all_queries_same_snapshot": True},
        "execution": "DATA ONLY; sole executor supplies authorized target, timeouts, actual IDs and private destination",
    }


_INTEGER_FIELDS = frozenset((
    "is_main", "new_cards_per_day", "canonical_prefix_revision", "scope_source_revision",
    "generation", "decision_index", "revision", "archived", "pending_validation",
    "canonical_route_source", "learner_decision_count", "source_revision", "initial_depth",
    "expected_revision", "in_scope", "ply", "first_decision_index", "last_decision_index",
    "disagreement_ply", "scope_start_ply",
))
_NULLABLE_FIELDS = frozenset((
    "new_cards_per_day", "canonical_prefix_preview_id", "last_error", "unlock_after_card_id",
    "superseded_by", "source_ref", "source_fen", "study_exercise_id", "reason", "origin_json", "parent_card_id",
    "disagreement_ply", "scope_start_ply",
))


def validate_graph_capture(
    capture, receipt, closure, *, expected_capture_sha256,
    expected_source_target_id, expected_selected_repertoire_ids,
):
    """Validate the fixed full-table copy and report selected graph usability.

    ``closure`` is the single owner's exact JSON file bytes. The expected raw
    capture hash must be computed from the original bytes (as this module's copy
    verifier does), never by reserializing ``capture``. Target identity and
    selected IDs come from explicit independently confirmed operator scope.
    Receipt structure consistency does not authenticate a database or executor.
    Its observed snapshot token applies only this optional SQL adapter; a native
    dump's successful single-snapshot invocation needs no new JSON token receipt.
    Extra original columns stay intact; no learning/history facts are migrated.
    Missing logical supersession targets are counted separately from enforced
    unlock/graph/split dependencies. Their original pointer is retained. Old
    serving eligibility may depend on that marker; this copy verifier grants no
    fresh-definition exception or serving approval.
    """
    if type(closure) is not bytes or hashlib.sha256(closure).hexdigest() != CLOSURE_SHA256:
        raise GraphTransferError("graph_capture_closure_changed")
    specification = _object(closure)
    if specification.get("source_commit") != SOURCE_REVISION:
        raise GraphTransferError("graph_capture_source_revision")
    _exact(capture, ("tables",))
    _exact(capture["tables"], CAPTURE_PRIMARY_KEYS)
    _exact(receipt, ("schema_version", "source_commit", "source_target_id",
                     "selected_repertoire_ids", "capture_sha256", "closure_sha256",
                     "consistent_read", "tables"))
    selected = receipt["selected_repertoire_ids"]
    expected_selected = expected_selected_repertoire_ids
    for identities in (selected, expected_selected):
        if (type(identities) is not list or not identities
                or any(type(value) is not str or not value for value in identities)
                or len(set(identities)) != len(identities)):
            raise GraphTransferError("graph_capture_explicit_unique_scope_required")
    if (type(receipt["schema_version"]) is not int or receipt["schema_version"] != 1
            or receipt["source_commit"] != SOURCE_REVISION
            or receipt["closure_sha256"] != CLOSURE_SHA256
            or receipt["capture_sha256"] != _digest(expected_capture_sha256)
            or type(expected_source_target_id) is not str or not expected_source_target_id
            or receipt["source_target_id"] != expected_source_target_id
            or set(selected) != set(expected_selected)):
        raise GraphTransferError("graph_capture_receipt_binding")
    transaction = receipt["consistent_read"]
    _exact(transaction, ("read_only", "isolation", "snapshot_id", "all_queries_same_snapshot"))
    if (transaction["read_only"] is not True or transaction["all_queries_same_snapshot"] is not True
            or transaction["isolation"] != "repeatable_read"
            or type(transaction["snapshot_id"]) is not str or not transaction["snapshot_id"]):
        raise GraphTransferError("graph_capture_one_consistent_read_required")
    _exact(receipt["tables"], CAPTURE_PRIMARY_KEYS)
    definitions = specification["required_capture_tables"]
    _exact(definitions, CAPTURE_PRIMARY_KEYS)
    indexed = {}
    for table, keys in CAPTURE_PRIMARY_KEYS.items():
        definition = definitions[table]
        if tuple(definition["primary_key"]) != keys or definition["capture_all_columns"] is not True:
            raise GraphTransferError("graph_capture_fixed_table_contract")
        rows = capture["tables"][table]
        facts = receipt["tables"][table]
        _exact(facts, ("row_count", "columns"))
        columns = facts["columns"]
        required = definition["required_graph_fields"]
        if (type(rows) is not list or type(facts["row_count"]) is not int
                or facts["row_count"] != len(rows) or type(columns) is not list
                or any(type(column) is not str for column in columns)
                or len(set(columns)) != len(columns) or not set(required) <= set(columns)):
            raise GraphTransferError("graph_capture_table_inventory")
        records = {}
        for row in rows:
            if type(row) is not dict or set(row) != set(columns):
                raise GraphTransferError("graph_capture_original_columns_required")
            for field in required:
                value = row[field]
                nullable = field in _NULLABLE_FIELDS or (table == "cards" and field in ("repertoire_id", "trained_color"))
                integer = field in _INTEGER_FIELDS or (table == "settings" and field == "id")
                if value is None and nullable:
                    continue
                if (integer and type(value) is not int) or (not integer and type(value) is not str):
                    raise GraphTransferError("graph_capture_required_field_type")
            key = tuple(row[field] for field in keys)
            if any(value is None or value == "" for value in key) or key in records:
                raise GraphTransferError("graph_capture_primary_key")
            records[key] = row
        indexed[table] = records
    reps = {key[0] for key in indexed["repertoires"]}
    cards = {key[0] for key in indexed["cards"]}
    lines = indexed["repertoire_lines"]
    previews = {key[0] for key in indexed["canonical_prefix_previews"]}
    if not set(selected) <= reps:
        raise GraphTransferError("graph_capture_selected_repertoire_missing")
    for table, fields, known in (
        ("repertoire_lines", ("repertoire_id",), reps),
        ("repertoire_cards", ("repertoire_id",), reps),
        ("repertoire_cards", ("card_id",), cards),
        ("opening_graph_publications", ("repertoire_id",), reps),
        ("opening_graph_steps", ("repertoire_id",), reps),
        ("prefix_splits", ("source_card_id", "shortened_card_id", "continuation_card_id"), cards),
        ("canonical_prefix_previews", ("repertoire_id",), reps),
        ("canonical_prefix_results", ("preview_id",), previews),
        ("canonical_prefix_positions", ("preview_id",), previews),
        ("opening_graph_legacy_mappings", ("repertoire_id",), reps),
        ("opening_graph_legacy_mappings", ("legacy_card_id", "decision_card_id"), cards),
    ):
        if any(row[field] not in known for row in indexed[table].values() for field in fields):
            raise GraphTransferError("graph_capture_source_reference_missing")
    for row in indexed["cards"].values():
        if ((row["repertoire_id"] is not None and row["repertoire_id"] not in reps)
                or (row["unlock_after_card_id"] is not None and row["unlock_after_card_id"] not in cards)):
            raise GraphTransferError("graph_capture_card_source_reference_missing")
    for row in indexed["repertoire_line_training_depths"].values():
        if (row["line_id"],) not in lines or row["learner_decision_count"] < 0:
            raise GraphTransferError("graph_capture_depth_reference")
    for row in indexed["opening_graph_steps"].values():
        line = lines.get((row["line_id"],))
        if (line is None or line["repertoire_id"] != row["repertoire_id"]
                or row["trained_color"] not in ("white", "black")
                or row["trained_color"] != line["trained_color"]
                or row["generation"] < 1 or row["decision_index"] < 0):
            raise GraphTransferError("graph_capture_graph_route_reference")
    blockers, publications = [], []
    for repertoire_id in sorted(selected):
        repertoire = indexed["repertoires"][(repertoire_id,)]
        preview_id = repertoire["canonical_prefix_preview_id"]
        if preview_id is not None and preview_id not in previews:
            blockers.append({"repertoire_id": repertoire_id, "code": "active_source_certificate_missing"})
        publication = indexed["opening_graph_publications"].get((repertoire_id,))
        if publication is None:
            blockers.append({"repertoire_id": repertoire_id, "code": "publication_missing"})
            continue
        graph = [row for row in indexed["opening_graph_steps"].values()
                 if row["repertoire_id"] == repertoire_id and row["generation"] == publication["generation"]]
        if publication["state"] != "ready" or not graph:
            blockers.append({"repertoire_id": repertoire_id, "code": "published_graph_unavailable"})
        for row in graph:
            if row["card_id"] not in cards or (row["parent_card_id"] is not None and row["parent_card_id"] not in cards):
                blockers.append({"repertoire_id": repertoire_id, "code": "published_definition_missing"})
        publications.append({"repertoire_id": repertoire_id, "generation": publication["generation"],
                             "state": publication["state"], "graph_rows": len(graph),
                             "graph_routes": len({row["line_id"] for row in graph}),
                             "graph_definitions": len({row["card_id"] for row in graph}),
                             "excluded_tombstones": len({row["card_id"] for row in graph}
                                                        & {key[0] for key in indexed["deleted_cards"]}),
                             "authored_routes": sum(row["repertoire_id"] == repertoire_id for row in lines.values())})
    settings = indexed["settings"].get((1,))
    if settings is None or settings["initial_depth"] < 1:
        blockers.append({"code": "source_default_depth_missing"})
    return {"capture_validated": True, "source_target_id": expected_source_target_id,
            "selected_repertoire_ids": sorted(selected), "selected_publications": publications,
            "usability_blockers": blockers, "depth_overrides": len(indexed["repertoire_line_training_depths"]),
            "split_overrides": len(indexed["prefix_splits"]), "tombstones": len(indexed["deleted_cards"]),
            "unresolved_supersession_lineage": sum(
                row["superseded_by"] is not None and row["superseded_by"] not in cards
                for row in indexed["cards"].values()
            ),
            "closure_sha256": CLOSURE_SHA256}


def _exact(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise GraphTransferError("graph_transfer_fields")


def _digest(value):
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise GraphTransferError("graph_transfer_digest")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise GraphTransferError("graph_transfer_duplicate_json_member")
        result[key] = value
    return result


def _reject_constant(_value):
    raise GraphTransferError("graph_transfer_nonfinite_json")


def _object(raw):
    if type(raw) is not bytes:
        raise GraphTransferError("graph_transfer_exact_bytes_required")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
        # Exponent overflow such as 1e999 does not call parse_constant.
        json.dumps(value, allow_nan=False)
    except (UnicodeError, ValueError, RecursionError):
        raise GraphTransferError("graph_transfer_strict_json_required") from None
    if type(value) is not dict:
        raise GraphTransferError("graph_transfer_json_object_required")
    return value


def _artifacts(artifacts):
    _exact(artifacts, ARTIFACT_NAMES)
    if any(type(raw) is not bytes or not raw for raw in artifacts.values()):
        raise GraphTransferError("graph_transfer_nonempty_exact_bytes_required")


def recovery_manifest_sha256(manifest):
    """Digest exact manifest facts with the existing canonical JSON convention."""
    try:
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise GraphTransferError("graph_transfer_invalid_manifest") from None
    return hashlib.sha256(encoded).hexdigest()


def create_recovery_manifest(artifacts):
    """Describe supplied immutable bytes; creating this is not copy verification."""
    _artifacts(artifacts)
    return {
        "schema_version": 1,
        "kind": "opening_graph_recovery_copy",
        "source_revision": SOURCE_REVISION,
        "artifacts": {name: {"sha256": hashlib.sha256(raw).hexdigest(),
                             "size_bytes": len(raw)}
                      for name, raw in artifacts.items()},
    }


def _capability(capability, *args, **kwargs):
    if not callable(capability):
        raise GraphTransferError("graph_transfer_capability_required")
    try:
        return capability(*args, **kwargs)
    except Exception:
        # Owner validators may include private rows, paths or target identities
        # in exceptions. Expose only this bounded failure at this public port.
        raise GraphTransferError("graph_transfer_owner_contract_rejected") from None


def verify_recovery_copy(
    manifest, artifacts, *, expected_manifest_sha256,
    inspect_capture, inspect_authored_import,
):
    """Check independently reread copy bytes against the retained manifest hash.

    ``inspect_capture(capture, receipt)`` must establish the closure
    owner's exact 86d11afe fields/PKs/selected publication generations and the
    sole executor's explicit source/selection/one-consistent-read receipt. The
    receipt must bind these exact capture bytes. It must not certify a paged
    capture assembled from unrelated transaction snapshots.

    ``inspect_authored_import(source_bytes, manifest)`` must use existing public
    Content contracts to validate SourceSnapshot v1, complete graph-only scope,
    explicit policy/color/ordered authored decisions and import provenance. It
    returns the existing Content canonical source SHA-256, not a new projection.
    The outer composition owns genuine origin/receipt verification; JSON labels
    and matching hashes alone do not authenticate them.
    """
    # Freeze original immutable byte references and manifest facts before any
    # injected capability runs; a callback cannot substitute a later document.
    _artifacts(artifacts)
    artifacts = dict(artifacts)
    manifest = copy.deepcopy(manifest)
    _exact(manifest, ("schema_version", "kind", "source_revision", "artifacts"))
    if (type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
            or manifest["kind"] != "opening_graph_recovery_copy"
            or manifest["source_revision"] != SOURCE_REVISION):
        raise GraphTransferError("graph_transfer_unsupported_source")
    manifest_digest = recovery_manifest_sha256(manifest)
    if manifest_digest != _digest(expected_manifest_sha256):
        raise GraphTransferError("graph_transfer_manifest_changed")
    _artifacts(artifacts)
    _exact(manifest["artifacts"], ARTIFACT_NAMES)
    for name in ARTIFACT_NAMES:
        expected = manifest["artifacts"][name]
        _exact(expected, ("sha256", "size_bytes"))
        if (type(expected["size_bytes"]) is not int or expected["size_bytes"] < 1
                or expected["size_bytes"] != len(artifacts[name])
                or _digest(expected["sha256"]) != hashlib.sha256(artifacts[name]).hexdigest()):
            raise GraphTransferError("graph_transfer_artifact_changed")
    capture = _object(artifacts["capture"])
    _exact(capture, ("tables",))
    if type(capture["tables"]) is not dict:
        raise GraphTransferError("graph_transfer_capture_shape")
    receipt = _object(artifacts["capture_receipt"])
    if receipt.get("capture_sha256") != manifest["artifacts"]["capture"]["sha256"]:
        raise GraphTransferError("graph_transfer_capture_receipt_hash")
    source = _object(artifacts["source"])
    import_manifest = _object(artifacts["import_manifest"])
    if (import_manifest.get("complete") is not True
            or import_manifest.get("legacy_aliases", {}) != {}
            or type(import_manifest.get("provenance")) is not dict
            or import_manifest["provenance"].get("source_sha256")
            != manifest["artifacts"]["source"]["sha256"]):
        raise GraphTransferError("graph_transfer_fresh_complete_import_required")
    capture_facts = _capability(inspect_capture, capture, receipt)
    if (
        type(capture_facts) is not dict or capture_facts.get("capture_validated") is not True
        or capture_facts.get("closure_sha256") != CLOSURE_SHA256
        or type(capture_facts.get("usability_blockers")) is not list
    ):
        raise GraphTransferError("graph_transfer_capture_unverified")
    source_digest = _digest(_capability(
        inspect_authored_import, artifacts["source"], import_manifest,
    ))
    if any(type(source.get(name)) is not str or not source[name]
           for name in ("workspace_id", "source_key")):
        raise GraphTransferError("graph_transfer_authored_identity_required")
    return {
        "copy_verified": True,
        "verification_scope": "supplied_json_translation_copy",
        "manifest_sha256": manifest_digest,
        "source_revision": SOURCE_REVISION,
        "capture_sha256": manifest["artifacts"]["capture"]["sha256"],
        "source_sha256": manifest["artifacts"]["source"]["sha256"],
        "source_digest": source_digest,
        "workspace_id": source["workspace_id"],
        "source_key": source["source_key"],
        "capture_facts": copy.deepcopy(capture_facts),
        "target_restored": False,
    }


def prepare_graph_import(
    manifest, artifacts, *, expected_manifest_sha256,
    inspect_capture, inspect_authored_import,
    target, validate_target, prepare_import,
):
    """Pass verified original bytes to an injected, side-effect-free import plan.

    The fresh-target owner supplies ``validate_target(target) is True`` after
    checking its exact disposable/initialized/empty target observation. The outer
    ``prepare_import(source_bytes, import_manifest, target=..., expected_revision=0)``
    builds existing public Content CLI input, then its V2 stage/activate plan.
    This helper executes neither CLI nor HTTP calls. Actual operation authority
    stays with those existing owners and separately authorized operator actions.
    """
    _artifacts(artifacts)
    artifacts = dict(artifacts)
    manifest = copy.deepcopy(manifest)
    target = copy.deepcopy(target)
    verified = verify_recovery_copy(
        manifest, artifacts, expected_manifest_sha256=expected_manifest_sha256,
        inspect_capture=inspect_capture, inspect_authored_import=inspect_authored_import,
    )
    if verified["capture_facts"]["usability_blockers"]:
        raise GraphTransferError("graph_transfer_source_usability_unresolved")
    if (type(target) is not dict or target.get("disposable") is not True
            or target.get("empty_learning") is not True
            or type(target.get("target_id")) is not str or not target["target_id"]
            or _capability(validate_target, copy.deepcopy(target)) is not True):
        raise GraphTransferError("graph_transfer_explicit_fresh_target_required")
    source = _object(artifacts["source"])
    if (type(source.get("workspace_id")) is not str or not source["workspace_id"]
            or source["workspace_id"] != target.get("workspace_id")):
        raise GraphTransferError("graph_transfer_target_workspace_mismatch")
    target_facts = copy.deepcopy(target)
    plan = _capability(
        prepare_import, artifacts["source"], _object(artifacts["import_manifest"]),
        target=copy.deepcopy(target_facts), expected_revision=0,
    )
    if type(plan) is not dict:
        raise GraphTransferError("graph_transfer_existing_import_plan_required")
    return {"recovery": verified, "target": target_facts, "import_plan": copy.deepcopy(plan)}


def verify_imported_graph(prepared, *, observed_source, inspect_restored_source):
    """Check independently observed full authored source using Content's digest.

    ``inspect_restored_source`` validates the existing full SourceSnapshot and
    returns Content's canonical source digest. Learning's reduced serving rows
    cannot substitute for this observation or for retained raw recovery files.
    Fresh activation/default-state/receipt verification has its own owner.
    """
    prepared = copy.deepcopy(prepared)
    if (type(prepared) is not dict or type(prepared.get("recovery")) is not dict
            or prepared["recovery"].get("copy_verified") is not True):
        raise GraphTransferError("graph_transfer_verified_preparation_required")
    expected = _digest(prepared["recovery"].get("source_digest"))
    observed = _digest(_capability(inspect_restored_source, copy.deepcopy(observed_source)))
    if observed != expected:
        raise GraphTransferError("graph_transfer_restored_source_mismatch")
    return {"authored_source_restored": True, "source_digest": expected,
            "manifest_sha256": prepared["recovery"]["manifest_sha256"],
            "workspace_id": prepared["recovery"]["workspace_id"],
            "source_key": prepared["recovery"]["source_key"],
            "fresh_learning_acceptance": "separate owner verification required"}
