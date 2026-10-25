"""Pure graph-copy boundary tests using fake owner capabilities, never real data."""

import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

from database.tooling import opening_graph_transfer as transfer


SOURCE = (Path(__file__).parent / "fixtures" / "opening_graph_transfer" / "source.json").read_bytes()
CLOSURE = (Path(__file__).resolve().parents[1] / "tooling" / "opening_graph_preservation" / "closure.json").read_bytes()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def source_digest(value):
    # A fake semantic port. Actual composition uses public Content contracts.
    return hashlib.sha256(encoded(value)).hexdigest()


def artifacts():
    return {
        # Deliberately minimal boundary fixtures, not the complete 14-table
        # legacy closure or evidence of an authorized actual capture.
        "capture": encoded({"tables": {"repertoires": [{"id": "synthetic-repertoire"}]}}),
        "capture_receipt": encoded({"evidence_kind": "synthetic", "single_snapshot": True,
                                    "capture_sha256": hashlib.sha256(encoded({"tables": {"repertoires": [{"id": "synthetic-repertoire"}]}})).hexdigest()}),
        "source": SOURCE,
        "import_manifest": encoded({
            "schema_version": 1, "kind": "authored_import", "complete": True,
            "workspace_id": "synthetic-fresh-workspace", "source_key": "synthetic-opening-source",
            "title": "Synthetic authored opening", "legacy_aliases": {}, "reentry": [],
            "provenance": {"kind": "authored_export", "source_sha256": hashlib.sha256(SOURCE).hexdigest(),
                           "description": "Synthetic graph boundary fixture", "evidence_refs": []},
        }),
    }


def target():
    return {"target_id": "synthetic-disposable-only", "workspace_id": "synthetic-fresh-workspace",
            "timezone": "Etc/UTC", "disposable": True, "initialized": True,
            "empty_learning": True, "observed_at": "2030-01-01T00:00:00Z"}


def full_capture():
    tables = {name: [] for name in transfer.CAPTURE_PRIMARY_KEYS}
    fen = json.loads(SOURCE)["routes"][0]["starting_fen"]
    tables["repertoires"] = [{"id": "synthetic-repertoire", "name": "Synthetic",
        "source_name": "Synthetic author", "created_at": "2030-01-01", "is_main": 1,
        "new_cards_per_day": None, "canonical_prefix_moves_json": "[]", "canonical_prefix_revision": 0,
        "canonical_prefix_preview_id": None, "scope_source_revision": 3}]
    tables["repertoire_lines"] = [{"id": "synthetic-line", "repertoire_id": "synthetic-repertoire",
        "name": "Synthetic route", "trained_color": "white", "start_fen": fen,
        "moves_json": '["e2e4", "e7e5"]', "created_at": "2030-01-01"}]
    tables["cards"] = [{"id": "synthetic-card", "repertoire_id": "synthetic-repertoire", "kind": "prefix",
        "start_fen": fen, "moves_json": '["e2e4", "e7e5"]', "unlock_after_card_id": None,
        "content_type": "opening", "archived": 0, "superseded_by": None, "source_ref": None,
        "source_fen": None, "revision": 8, "trained_color": "white", "pending_validation": 0,
        "canonical_route_source": 1, "study_exercise_id": None, "due_date": "1900-01-01"}]
    tables["repertoire_cards"] = [{"repertoire_id": "synthetic-repertoire", "card_id": "synthetic-card",
                                   "canonical_route_source": 1}]
    tables["opening_graph_publications"] = [{"repertoire_id": "synthetic-repertoire", "generation": 7,
        "state": "ready", "last_error": None, "published_at": "2030-01-01"}]
    graph = {"repertoire_id": "synthetic-repertoire", "generation": 7, "line_id": "synthetic-line",
        "decision_index": 0, "card_id": "synthetic-card", "parent_card_id": None,
        "decision_fen_key": "synthetic-position-key", "starting_fen": fen,
        "moves_json": '["e2e4", "e7e5"]', "trained_color": "white", "segment_kind": "prefix",
        "first_decision_index": 0, "last_decision_index": 0, "decision_fen_keys_json": '["synthetic-position-key"]'}
    tables["opening_graph_steps"] = [graph, {**graph, "generation": 9}]
    tables["settings"] = [{"id": 1, "initial_depth": 6}]
    return {"tables": tables}


def capture_receipt(capture):
    definitions = json.loads(CLOSURE)["required_capture_tables"]
    return {
        "schema_version": 1, "source_commit": transfer.SOURCE_REVISION,
        "source_target_id": "synthetic-explicit-source", "selected_repertoire_ids": ["synthetic-repertoire"],
        "capture_sha256": hashlib.sha256(encoded(capture)).hexdigest(), "closure_sha256": transfer.CLOSURE_SHA256,
        "consistent_read": {"read_only": True, "isolation": "repeatable_read", "snapshot_id": "synthetic-snapshot",
                            "all_queries_same_snapshot": True},
        "tables": {name: {"row_count": len(rows), "columns": list(rows[0]) if rows else definitions[name]["required_graph_fields"]}
                   for name, rows in capture["tables"].items()},
    }


class FixedGraphCaptureTests(unittest.TestCase):
    def validate(self, capture=None, receipt=None, **kwargs):
        capture = full_capture() if capture is None else capture
        receipt = capture_receipt(capture) if receipt is None else receipt
        values = dict(expected_capture_sha256=hashlib.sha256(encoded(capture)).hexdigest(),
                      expected_source_target_id="synthetic-explicit-source",
                      expected_selected_repertoire_ids=["synthetic-repertoire"])
        values.update(kwargs)
        return transfer.validate_graph_capture(capture, receipt, CLOSURE, **values)

    def test_fixed_plan_is_only_fourteen_full_table_reads_without_rescans(self):
        plan = transfer.capture_query_plan()
        self.assertEqual(len(plan["queries"]), 14)
        self.assertEqual(plan["closure_sha256"], hashlib.sha256(CLOSURE).hexdigest())
        for query in plan["queries"]:
            self.assertTrue(query["sql"].startswith("SELECT * FROM public."))
            self.assertNotIn("WHERE", query["sql"])
            self.assertNotIn("COUNT", query["sql"])
            self.assertNotIn("LIMIT", query["sql"])
        self.assertEqual(plan["end"], "ROLLBACK")
        self.assertIn("REPEATABLE READ READ ONLY", plan["begin"])

    def test_copy_retains_extra_original_fields_and_selects_published_not_latest_generation(self):
        capture = full_capture()
        original = copy.deepcopy(capture)
        facts = self.validate(capture)
        self.assertEqual(capture, original)
        self.assertTrue(facts["capture_validated"])
        self.assertEqual(facts["usability_blockers"], [])
        self.assertEqual(facts["selected_publications"][0]["generation"], 7)
        self.assertEqual(facts["selected_publications"][0]["graph_rows"], 1)
        self.assertEqual(capture["tables"]["cards"][0]["revision"], 8)
        self.assertEqual(capture["tables"]["cards"][0]["due_date"], "1900-01-01")

    def test_missing_table_required_column_duplicate_pk_or_bool_flag_fails(self):
        for kind in ("table", "field", "pk", "type"):
            capture = full_capture()
            if kind == "table":
                del capture["tables"]["prefix_splits"]
                receipt = capture_receipt(full_capture())
            elif kind == "field":
                del capture["tables"]["cards"][0]["canonical_route_source"]
                receipt = capture_receipt(capture)
            elif kind == "pk":
                capture["tables"]["cards"] *= 2
                receipt = capture_receipt(capture)
            else:
                capture["tables"]["cards"][0]["archived"] = False
                receipt = capture_receipt(capture)
            with self.subTest(kind=kind), self.assertRaises(transfer.GraphTransferError):
                self.validate(capture, receipt)

    def test_receipt_binds_bytes_source_target_explicit_scope_and_one_snapshot(self):
        for field, value in (("capture_sha256", "0" * 64), ("closure_sha256", "0" * 64),
                             ("source_commit", "unknown"), ("source_target_id", "another-source"),
                             ("selected_repertoire_ids", []), ("schema_version", True)):
            receipt = capture_receipt(full_capture())
            receipt[field] = value
            with self.subTest(field=field), self.assertRaises(transfer.GraphTransferError):
                self.validate(receipt=receipt)
        for field, value in (("read_only", False), ("isolation", "read_committed"),
                             ("all_queries_same_snapshot", False), ("snapshot_id", "")):
            receipt = capture_receipt(full_capture())
            receipt["consistent_read"][field] = value
            with self.subTest(field=field), self.assertRaises(transfer.GraphTransferError):
                self.validate(receipt=receipt)

    def test_stream_inventory_must_match_every_captured_row(self):
        for kind in ("count", "columns", "row_columns"):
            capture = full_capture()
            receipt = capture_receipt(capture)
            if kind == "count":
                receipt["tables"]["cards"]["row_count"] += 1
            elif kind == "columns":
                receipt["tables"]["cards"]["columns"].append("absent_original_column")
            else:
                capture["tables"]["cards"][0]["missing_from_stream_metadata"] = 1
            with self.assertRaises(transfer.GraphTransferError):
                self.validate(capture, receipt)

    def test_source_references_are_closed_without_foreign_nonopening_history(self):
        capture = full_capture()
        capture["tables"]["repertoire_cards"][0]["card_id"] = "missing"
        with self.assertRaises(transfer.GraphTransferError):
            self.validate(capture)
        capture = full_capture()
        extra = {**capture["tables"]["cards"][0], "id": "synthetic-nonopening",
                 "content_type": "study_exercise", "repertoire_id": None,
                 "study_exercise_id": "outside-graph-only-scope"}
        capture["tables"]["cards"].append(extra)
        self.assertEqual(self.validate(capture)["usability_blockers"], [])

    def test_failed_or_missing_published_definitions_are_reported_without_dropping_copy(self):
        capture = full_capture()
        capture["tables"]["opening_graph_publications"][0]["state"] = "failed"
        facts = self.validate(capture)
        self.assertTrue(facts["capture_validated"])
        self.assertIn("published_graph_unavailable", [item["code"] for item in facts["usability_blockers"]])
        capture = full_capture()
        capture["tables"]["opening_graph_steps"][0]["parent_card_id"] = "missing-logical-parent"
        self.assertIn("published_definition_missing", [item["code"] for item in self.validate(capture)["usability_blockers"]])

    def test_depth_override_zero_and_absent_card_tombstones_survive_as_source_facts(self):
        capture = full_capture()
        capture["tables"]["repertoire_line_training_depths"] = [{"line_id": "synthetic-line", "learner_decision_count": 0}]
        capture["tables"]["deleted_cards"] = [{"card_id": "synthetic-deleted", "deleted_at": " raw-original-timestamp "}]
        facts = self.validate(capture)
        self.assertEqual(facts["depth_overrides"], 1)
        self.assertEqual(facts["tombstones"], 1)
        self.assertEqual(capture["tables"]["deleted_cards"][0]["deleted_at"], " raw-original-timestamp ")


class OpeningGraphTransferTests(unittest.TestCase):
    def setUp(self):
        self.artifacts = artifacts()
        self.manifest = transfer.create_recovery_manifest(self.artifacts)
        self.manifest_hash = transfer.recovery_manifest_sha256(self.manifest)
        self.capture_inspector = Mock(return_value={"capture_validated": True,
            "closure_sha256": transfer.CLOSURE_SHA256, "usability_blockers": []})
        self.source_inspector = Mock(side_effect=lambda raw, _manifest: source_digest(json.loads(raw)))

    def verify(self, **overrides):
        values = dict(manifest=self.manifest, artifacts=self.artifacts,
                      expected_manifest_sha256=self.manifest_hash,
                      inspect_capture=self.capture_inspector,
                      inspect_authored_import=self.source_inspector)
        values.update(overrides)
        return transfer.verify_recovery_copy(**values)

    def prepare(self, **overrides):
        values = dict(manifest=self.manifest, artifacts=self.artifacts,
                      expected_manifest_sha256=self.manifest_hash,
                      inspect_capture=self.capture_inspector,
                      inspect_authored_import=self.source_inspector,
                      target=target(), validate_target=lambda _target: True,
                      prepare_import=lambda *args, **kwargs: {"operation_id": "synthetic-content-operation"})
        values.update(overrides)
        return transfer.prepare_graph_import(**values)

    def repack(self, name, value):
        self.artifacts[name] = encoded(value)
        self.manifest = transfer.create_recovery_manifest(self.artifacts)
        self.manifest_hash = transfer.recovery_manifest_sha256(self.manifest)

    def test_copy_readback_verifies_exact_original_bytes_without_mutation(self):
        original = copy.deepcopy((self.manifest, self.artifacts))
        result = self.verify(artifacts=copy.deepcopy(self.artifacts))
        self.assertEqual(result, self.verify())
        self.assertEqual((self.manifest, self.artifacts), original)
        self.assertTrue(result["copy_verified"])
        self.assertEqual(result["verification_scope"], "supplied_json_translation_copy")
        self.assertFalse(result["target_restored"])
        self.assertEqual(result["workspace_id"], "synthetic-fresh-workspace")
        self.assertEqual(result["source_key"], "synthetic-opening-source")

    def test_every_changed_artifact_fails_before_owner_inspection(self):
        for name in transfer.ARTIFACT_NAMES:
            changed = self.artifacts.copy()
            changed[name] += b" "
            with self.subTest(name=name), self.assertRaises(transfer.GraphTransferError):
                self.verify(artifacts=changed)
        self.capture_inspector.assert_not_called()
        self.source_inspector.assert_not_called()

    def test_manifest_change_cannot_be_self_approved_by_changed_file_hashes(self):
        changed = copy.deepcopy(self.manifest)
        changed["artifacts"]["capture"]["sha256"] = "0" * 64
        with self.assertRaises(transfer.GraphTransferError):
            self.verify(manifest=changed)
        self.capture_inspector.assert_not_called()

    def test_unknown_missing_or_nonbyte_artifacts_are_rejected(self):
        for change in ({"unknown": b"{}"}, {"source": "not exact bytes"}, {"source": b""}):
            with self.assertRaises(transfer.GraphTransferError):
                transfer.create_recovery_manifest({**self.artifacts, **change})
        del self.artifacts["capture_receipt"]
        with self.assertRaises(transfer.GraphTransferError):
            transfer.create_recovery_manifest(self.artifacts)

    def test_wrong_version_revision_size_and_extra_manifest_fields_fail(self):
        for field, value in (("schema_version", True), ("schema_version", 2),
                             ("source_revision", "unobserved"), ("extra", None)):
            changed = copy.deepcopy(self.manifest)
            changed[field] = value
            with self.assertRaises(transfer.GraphTransferError):
                self.verify(manifest=changed,
                            expected_manifest_sha256=transfer.recovery_manifest_sha256(changed))
        changed = copy.deepcopy(self.manifest)
        changed["artifacts"]["source"]["size_bytes"] = True
        with self.assertRaises(transfer.GraphTransferError):
            self.verify(manifest=changed,
                        expected_manifest_sha256=transfer.recovery_manifest_sha256(changed))

    def test_capture_owner_must_verify_consistency_and_exact_closure(self):
        for result in (False, True, None, 1, {"verified": True}):
            with self.assertRaises(transfer.GraphTransferError):
                self.verify(inspect_capture=lambda *args: result)
        self.source_inspector.assert_not_called()

    def test_owner_exception_is_redacted_and_prevents_following_operations(self):
        secret = "synthetic-private-capture-and-target"
        failing = Mock(side_effect=ValueError(secret))
        with self.assertRaises(transfer.GraphTransferError) as error:
            self.verify(inspect_capture=failing)
        self.assertNotIn(secret, str(error.exception))
        self.source_inspector.assert_not_called()

    def test_source_validator_requires_existing_canonical_source_digest(self):
        for digest in (None, True, "0" * 63, "A" * 64, {}):
            with self.assertRaises(transfer.GraphTransferError):
                self.verify(inspect_authored_import=lambda *args: digest)

    def test_raw_source_provenance_binding_is_exact_not_reserialized(self):
        self.artifacts["source"] = encoded(json.loads(SOURCE))
        self.manifest = transfer.create_recovery_manifest(self.artifacts)
        self.manifest_hash = transfer.recovery_manifest_sha256(self.manifest)
        with self.assertRaises(transfer.GraphTransferError):
            self.verify()

    def test_incomplete_scope_or_historical_aliases_cannot_enter_fresh_import(self):
        original = json.loads(self.artifacts["import_manifest"])
        for field, value in (("complete", False), ("complete", 1),
                             ("legacy_aliases", {"new-card": "old-card"})):
            self.repack("import_manifest", {**original, field: value})
            with self.assertRaises(transfer.GraphTransferError):
                self.verify()

    def test_duplicate_nonfinite_overflow_and_nonobject_json_fail(self):
        for invalid in (b'{"tables":{},"tables":{},"graphs":[]}', b'{"x":NaN}',
                        b'{"x":1e999}', b'[]', b'\xff'):
            changed = self.artifacts.copy()
            changed["capture"] = invalid
            manifest = transfer.create_recovery_manifest(changed)
            with self.assertRaises(transfer.GraphTransferError):
                self.verify(manifest=manifest, artifacts=changed,
                            expected_manifest_sha256=transfer.recovery_manifest_sha256(manifest))

    def test_capture_retains_raw_tables_separately_from_downstream_graph_projection(self):
        for capture in ({"tables": {}, "graphs": []},
                        {"tables": []}, {"tables": {}, "history": []}):
            self.repack("capture", capture)
            with self.assertRaises(transfer.GraphTransferError):
                self.verify()

    def test_existing_import_planner_gets_original_bytes_and_revision_zero(self):
        planner = Mock(return_value={"operation_id": "synthetic-content-operation"})
        result = self.prepare(prepare_import=planner)
        args, kwargs = planner.call_args
        self.assertEqual(args, (SOURCE, json.loads(self.artifacts["import_manifest"])))
        self.assertEqual(kwargs, {"target": target(), "expected_revision": 0})
        self.assertEqual(result["import_plan"], {"operation_id": "synthetic-content-operation"})

    def test_missing_ambiguous_or_nonempty_target_prevents_import_planning(self):
        planner = Mock()
        for invalid in (None, {}, {**target(), "target_id": ""},
                        {**target(), "empty_learning": False}, {**target(), "disposable": False}):
            with self.assertRaises(transfer.GraphTransferError):
                self.prepare(target=invalid, prepare_import=planner)
        with self.assertRaises(transfer.GraphTransferError):
            self.prepare(validate_target=lambda _target: False, prepare_import=planner)
        planner.assert_not_called()

    def test_workspace_mismatch_fails_without_relabeling_the_source(self):
        planner = Mock()
        with self.assertRaises(transfer.GraphTransferError):
            self.prepare(target={**target(), "workspace_id": "other"}, prepare_import=planner)
        planner.assert_not_called()

    def test_planner_mutation_cannot_change_retained_target_or_recovery_artifacts(self):
        original = copy.deepcopy((self.artifacts, self.manifest))
        destination = target()
        def planner(_raw, imported, **kwargs):
            kwargs["target"]["target_id"] = "changed"
            imported["complete"] = False
            return {"operation_id": "synthetic-operation"}
        prepared = self.prepare(target=destination, prepare_import=planner)
        self.assertEqual(prepared["target"], target())
        self.assertEqual(destination, target())
        self.assertEqual((self.artifacts, self.manifest), original)

    def test_injected_inspector_cannot_substitute_artifacts_after_byte_verification(self):
        original_manifest_hash = self.manifest_hash
        def inspector(*_args):
            self.artifacts["source"] = b'{"workspace_id":"substituted"}'
            self.manifest["artifacts"]["source"]["sha256"] = "0" * 64
            return {"capture_validated": True, "closure_sha256": transfer.CLOSURE_SHA256,
                    "usability_blockers": []}
        planner = Mock(return_value={"operation_id": "synthetic-operation"})
        prepared = self.prepare(inspect_capture=inspector, prepare_import=planner)
        self.assertEqual(planner.call_args.args[0], SOURCE)
        self.assertEqual(prepared["recovery"]["manifest_sha256"], original_manifest_hash)
        self.assertEqual(prepared["recovery"]["source_sha256"], hashlib.sha256(SOURCE).hexdigest())

    def test_restore_inspector_cannot_rebind_retained_preparation_metadata(self):
        prepared = self.prepare()
        original = copy.deepcopy(prepared)
        def inspector(observed):
            prepared["recovery"]["manifest_sha256"] = "0" * 64
            prepared["recovery"]["workspace_id"] = "substituted"
            return source_digest(observed)
        restored = transfer.verify_imported_graph(prepared, observed_source=json.loads(SOURCE),
                                                   inspect_restored_source=inspector)
        self.assertEqual(restored["manifest_sha256"], original["recovery"]["manifest_sha256"])
        self.assertEqual(restored["workspace_id"], original["recovery"]["workspace_id"])

    def test_repeated_preparation_is_only_a_pure_plan_not_import_replay_evidence(self):
        self.assertEqual(self.prepare(), self.prepare())
        self.assertFalse(self.prepare()["recovery"]["target_restored"])

    def test_full_authored_source_restore_uses_injected_existing_semantic_digest(self):
        prepared = self.prepare()
        observation = json.loads(SOURCE)
        result = transfer.verify_imported_graph(prepared, observed_source=observation,
                                                 inspect_restored_source=source_digest)
        self.assertTrue(result["authored_source_restored"])
        self.assertEqual(result["source_digest"], prepared["recovery"]["source_digest"])
        self.assertIn("separate owner", result["fresh_learning_acceptance"])

    def test_missing_route_or_changed_raw_fen_cannot_pass_full_source_restore(self):
        prepared = self.prepare()
        for change in ("routes", "fen"):
            observation = json.loads(SOURCE)
            if change == "routes":
                observation["routes"] = []
            else:
                observation["routes"][0]["starting_fen"] += " "
            with self.assertRaises(transfer.GraphTransferError):
                transfer.verify_imported_graph(prepared, observed_source=observation,
                                                 inspect_restored_source=source_digest)

    def test_reduced_learning_rows_are_not_full_authored_restore(self):
        with self.assertRaises(transfer.GraphTransferError):
            transfer.verify_imported_graph(self.prepare(), observed_source={"cards": []},
                                             inspect_restored_source=source_digest)


if __name__ == "__main__":
    unittest.main()
