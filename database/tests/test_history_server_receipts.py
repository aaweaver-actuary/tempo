"""Synthetic-only retained server work preparation; no authority or live I/O."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tooling" / "history_server_receipts.py"
SPEC = importlib.util.spec_from_file_location("history_server_receipts", TOOL)
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)
FIXTURE = ROOT / "tests" / "fixtures" / "history_server_receipts" / "synthetic-source.json"


def source():
    return json.loads(FIXTURE.read_text())


def synthetic_approval(packet):
    # Explicit test fake. This is neither #389 approval nor actual-data proof.
    return packet["authority"] == {"synthetic_only": True}


def convert(packet):
    return server.convert_server_receipts(packet, approval=synthetic_approval)


class HistoryServerReceiptsTests(unittest.TestCase):
    def test_preserves_original_answer_receipt_recovery_and_unknown_issuance(self):
        packet = source()
        original = copy.deepcopy(packet)
        result = convert(packet)
        self.assertEqual(set(result), {"queue_attempts", "operation_receipts", "introductions"})
        self.assertEqual([len(result[group]) for group in server.OUTPUT_GROUPS], [4, 2, 1])
        indexed = {record["key"]: record["data"] for records in result.values() for record in records}
        self.assertEqual(indexed["synthetic:attempt:old"]["study_attempts"], packet["tables"]["study_attempts"][0]["row"])
        self.assertEqual(indexed["synthetic:origin:7-card-r2"]["first_issued_provenance"], "unknown")
        receipt = indexed["synthetic:receipt:original"]
        self.assertEqual(receipt["operation_receipts"]["payload_json"], ' {"attempt_id":"synthetic-attempt"} ')
        self.assertEqual(receipt["operation_receipts"]["request_hash"], "synthetic-original-request-hash")
        self.assertEqual(receipt["cold"]["operation_receipts"], {
            "background": False, "attempt_count": 2, "lease_expires_at": None,
            "last_error_json": ' {"error":"original-timeout"} ', "retry_cycle": 1,
            "cycle_attempt_count": 1, "attempt_token": "synthetic-retired-token",
        })
        self.assertIsNone(indexed["synthetic:review-receipt:original"]["review_attempt_receipts"]["request_json"])
        self.assertEqual(packet, original)
        result["queue_attempts"][0]["data"].clear()
        self.assertEqual(packet, original)

    def test_teaching_keeps_original_revision_and_ply(self):
        result = convert(source())["introductions"]
        self.assertEqual(result, [{"key": "synthetic:teaching:card-r1-p0", "data": {
            "teaching_states": {"card_id": "synthetic-card", "revision": 1, "ply": 0,
                                "taught_at": "2030-01-01T08:00:00+02:00"}}}])

    def test_repeat_and_row_reordering_are_deterministic_without_logical_effects(self):
        packet = source()
        first = convert(packet)
        for records in packet["tables"].values():
            records.reverse()
        self.assertEqual(first, convert(packet))
        self.assertEqual(first, convert(packet))

    def test_approval_binds_entire_packet_and_must_return_literal_true(self):
        packet = source()
        observed = []
        def approval(value):
            observed.append(copy.deepcopy(value))
            value.clear()  # A validator must not obtain mutable ownership.
            return True
        server.convert_server_receipts(packet, approval=approval)
        self.assertEqual(observed, [packet])
        for response in (False, None, 1, "approved"):
            with self.subTest(response=response), self.assertRaisesRegex(server.ServerReceiptError, "approval_required"):
                server.convert_server_receipts(packet, approval=lambda value: response)

    def test_unknown_authority_is_not_synthetic_approval(self):
        packet = source()
        packet["authority"] = {"unknown": "synthetic-private-authority"}
        with self.assertRaisesRegex(server.ServerReceiptError, "approval_required") as error:
            convert(packet)
        self.assertNotIn("synthetic-private-authority", str(error.exception))

    def test_unresolved_or_unreviewed_packet_is_rejected_before_approval(self):
        for changed in ({"unresolved": ["unknown"]}, {"schema_version": True}, {"extra": []}):
            packet = source()
            packet.update(changed)
            with self.subTest(changed=changed), self.assertRaises(server.ServerReceiptError):
                server.convert_server_receipts(packet, approval=lambda value: self.fail("must not approve"))

    def test_missing_empty_table_and_unknown_table_fail_closed(self):
        for operation in (lambda tables: tables.pop("remediation_cards"),
                          lambda tables: tables.update({"new_installed_table": []})):
            packet = source()
            operation(packet["tables"])
            with self.assertRaisesRegex(server.ServerReceiptError, "inventory_unreviewed"):
                convert(packet)

    def test_unknown_or_missing_column_is_not_dropped(self):
        for operation in (lambda row: row.pop("answer_hash"),
                          lambda row: row.update({"new_installed_field": "retained"})):
            packet = source()
            operation(packet["tables"]["study_attempts"][0]["row"])
            with self.assertRaisesRegex(server.ServerReceiptError, "columns_unreviewed:study_attempts"):
                convert(packet)

    def test_boolean_bigint_and_text_json_are_not_coerced(self):
        for table, column, value in (("daily_queue", "attempt_failed", True),
                                     ("operation_receipts", "background", 0),
                                     ("study_attempts", "answer_json", {"moves": []}),
                                     ("daily_queue", "id", "7")):
            packet = source()
            packet["tables"][table][0]["row"][column] = value
            with self.subTest(table=table, column=column), self.assertRaisesRegex(server.ServerReceiptError, "type_unreviewed"):
                convert(packet)

    def test_duplicate_keys_primary_keys_and_queue_identity_conflicts_stop(self):
        for change in ("same-key", "same-pk", "same-queue-tuple"):
            packet = source()
            records = packet["tables"]["daily_queue"]
            extra = copy.deepcopy(records[0])
            if change != "same-key": extra["key"] = "synthetic:other-key"
            if change == "same-key": extra["row"]["id"] = 8
            if change == "same-queue-tuple": extra["row"]["id"] = 8
            records.append(extra)
            with self.subTest(change=change), self.assertRaisesRegex(server.ServerReceiptError, "identity"):
                convert(packet)

    def test_active_server_receipts_require_original_recovery_owner(self):
        for state in ("pending", "queued", "executing", "retrying", "blocked", "unknown-state"):
            packet = source()
            packet["tables"]["operation_receipts"][0]["row"]["state"] = state
            with self.subTest(state=state), self.assertRaisesRegex(server.ServerReceiptError, "original_recovery_authority_required"):
                convert(packet)

    def test_terminal_missing_payload_is_not_filled_with_empty_object(self):
        packet = source()
        packet["tables"]["operation_receipts"][0]["row"]["payload_json"] = None
        with self.assertRaisesRegex(server.ServerReceiptError, "payload_missing"):
            convert(packet)

    def test_active_saved_attempt_is_preserved_without_regrading_or_finalizing(self):
        result = convert(source())
        attempt = next(row["data"]["study_attempts"] for row in result["queue_attempts"] if "study_attempts" in row["data"])
        self.assertIsNone(attempt["finalized_at"])
        self.assertIsNone(attempt["result_json"])
        self.assertEqual(attempt["grader_version"], 4)
        self.assertEqual(attempt["answer_json"], ' {"moves": ["e2e4"]} ')

    def test_remediation_workspace_is_not_relabelled_or_authority_transferred(self):
        packet = source()
        packet["tables"]["remediation_workspaces"] = [{"key": "synthetic:legacy-workspace", "row": {"workspace_id": "legacy", "revision": 3}}]
        with self.assertRaisesRegex(server.ServerReceiptError, "authority_transfer_unapproved"):
            convert(packet)

    def test_outstanding_fence_and_unready_projection_block_entire_leaf(self):
        for table, row, error in (
            ("prefix_transition_card_fences", {"card_id": "synthetic-card", "operation_id": "synthetic-op"}, "outstanding_transition_fence"),
            ("queue_projections", {"queue_date": "2030-01-02", "state": "failed", "generation": 2, "updated_at": None, "refresh_pending": 1, "last_error": "synthetic-private-failure", "blocked_count": 1}, "projection_reconciliation_required"),
        ):
            packet = source()
            packet["tables"][table] = [{"key": "synthetic:blocker", "row": row}]
            with self.subTest(table=table), self.assertRaisesRegex(server.ServerReceiptError, error) as caught:
                convert(packet)
            self.assertNotIn("synthetic-private-failure", str(caught.exception))

    def test_nullable_defense_queue_identity_is_never_invented(self):
        packet = source()
        packet["tables"]["defense_attempts"] = [{"key": "synthetic:defense", "row": {
            "attempt_id": "synthetic-defense", "candidate_id": "synthetic-candidate", "card_id": "synthetic-card",
            "queue_entry_id": None, "exercise_revision": 1, "move_uci": "e2e4", "grade_json": "{}", "reviewed_at": "2030-01-02"}}]
        with self.assertRaisesRegex(server.ServerReceiptError, "queue_identity_missing"):
            convert(packet)

    def test_terminal_transition_is_preserved_cold_without_rerunning_its_plan(self):
        packet = source()
        row = {
            "operation_id": "synthetic-terminal-operation", "repertoire_id": "synthetic-repertoire",
            "plan_id": "synthetic-plan", "plan_json": ' {"keep":"original"} ', "graph_generation": 4,
            "state": "complete", "staging_task_id": None, "graph_task_id": None,
            "integrity_task_id": None, "integrity_generation": None, "queue_task_id": None,
            "queue_generation": None, "queue_date": None, "result_json": "{}", "last_error": None,
            "created_at": "2030-01-01T01:00:00Z", "updated_at": "2030-01-02T01:00:00Z",
        }
        packet["tables"]["prefix_transition_applications"] = [{"key": "synthetic:terminal-transition", "row": row}]
        result = convert(packet)
        preserved = next(record["data"] for record in result["operation_receipts"] if record["key"] == "synthetic:terminal-transition")
        self.assertEqual(preserved, {"cold": {"prefix_transition_applications": row}})
        packet["tables"]["prefix_transition_applications"][0]["row"]["state"] = "publishing"
        with self.assertRaisesRegex(server.ServerReceiptError, "transition_original_owner_required"):
            convert(packet)

    def test_background_event_requires_original_owner_terminal_disposition(self):
        packet = source()
        packet["tables"]["background_task_events"] = [{"key": "synthetic:event", "row": {
            "id": 8, "task_id": "synthetic-task", "generation": 2, "event": "complete",
            "phase": None, "detail": None, "created_at": "2030-01-02"}}]
        with self.assertRaisesRegex(server.ServerReceiptError, "background_terminal_disposition_unapproved"):
            convert(packet)

    def test_invalid_issuance_flag_does_not_become_historical_provenance(self):
        packet = source()
        packet["tables"]["queue_attempt_origins"][0]["row"]["issued_as_head"] = 2
        with self.assertRaisesRegex(server.ServerReceiptError, "flag_unreviewed"):
            convert(packet)

    def test_no_runtime_adapter_sibling_converter_or_service_import(self):
        imports = [node for node in ast.walk(ast.parse(TOOL.read_text())) if isinstance(node, (ast.Import, ast.ImportFrom))]
        names = {alias.name for node in imports if isinstance(node, ast.Import) for alias in node.names}
        names.update(node.module for node in imports if isinstance(node, ast.ImportFrom))
        self.assertEqual(names, {"copy", "math", "typing"})


if __name__ == "__main__":
    unittest.main()
