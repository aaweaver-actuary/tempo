"""Synthetic, standard-library-only coverage of normalized snapshot comparison."""

import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch


# Load only the standalone utility, without importing application packages.
_TOOL_PATH = Path(__file__).resolve().parents[1] / "tooling" / "study_snapshot_compare.py"
_SPEC = importlib.util.spec_from_file_location("study_snapshot_compare", _TOOL_PATH)
compare = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(compare)

GROUPS = (
    "cards", "reviews", "schedules", "introductions", "accepted_definitions",
    "queue_attempts", "operation_receipts", "browser_intents",
)


def snapshot():
    return {
        "schema_version": 1,
        "workspace_id": "synthetic-workspace",
        "unresolved": [],
        "entities": {group: [] for group in GROUPS},
    }


def populated_snapshot():
    document = snapshot()
    for group in GROUPS:
        document["entities"][group] = [
            {"key": f"synthetic-{group}-b", "data": {"revision": 2, "nested": {"b": 2, "a": 1}}},
            {"key": f"synthetic-{group}-a", "data": {"revision": 1, "events": [1, True, None]}},
        ]
    return document


class StudySnapshotCompareTests(unittest.TestCase):
    def test_identical_documents_are_equal_without_mutation(self):
        before = populated_snapshot()
        original = copy.deepcopy(before)
        result = compare.compare_snapshots(before, copy.deepcopy(before))
        self.assertEqual(result, {
            "equal": True,
            "entities": {group: {"missing": [], "extra": [], "changed": []} for group in GROUPS},
        })
        self.assertEqual(before, original)

    def test_reordered_rows_and_object_members_are_equal(self):
        before = populated_snapshot()
        after = copy.deepcopy(before)
        after["entities"] = dict(reversed(list(after["entities"].items())))
        for rows in after["entities"].values():
            rows.reverse()
            rows[1]["data"]["nested"] = {"a": 1, "b": 2}
        self.assertTrue(compare.compare_snapshots(before, after)["equal"])

    def test_removed_review_reports_missing_key(self):
        before = populated_snapshot()
        after = copy.deepcopy(before)
        after["entities"]["reviews"].pop()
        result = compare.compare_snapshots(before, after)
        self.assertFalse(result["equal"])
        self.assertEqual(result["entities"]["reviews"], {
            "missing": ["synthetic-reviews-a"], "extra": [], "changed": [],
        })

    def test_changed_schedule_keeps_dates_revisions_and_unknown_fields(self):
        before = snapshot()
        before["entities"]["schedules"] = [{"key": "schedule-1", "data": {
            "due": "2030-01-01", "revision": 3, "future_field": {"stability": 1.25},
        }}]
        after = copy.deepcopy(before)
        after["entities"]["schedules"][0]["data"] = {
            "due": "2030-01-02", "revision": 4, "future_field": {"stability": 2.5},
        }
        self.assertEqual(compare.compare_snapshots(before, after)["entities"]["schedules"]["changed"], [
            {"key": "schedule-1", "paths": ["/due", "/future_field/stability", "/revision"]},
        ])

    def test_changed_operation_payload_reports_paths_without_values(self):
        before = snapshot()
        before["entities"]["operation_receipts"] = [{"key": "receipt-1", "data": {
            "payload": {"secret": "synthetic-private-before", "moves": ["e4", "e5"]},
        }}]
        after = copy.deepcopy(before)
        after["entities"]["operation_receipts"][0]["data"]["payload"] = {
            "secret": "synthetic-private-after", "moves": ["e4", "c5"],
        }
        result = compare.compare_snapshots(before, after)
        self.assertEqual(result["entities"]["operation_receipts"]["changed"], [
            {"key": "receipt-1", "paths": ["/payload/moves/1", "/payload/secret"]},
        ])
        rendered = json.dumps(result)
        for value in ("synthetic-private-before", "synthetic-private-after", "e4", "e5", "c5", "synthetic-workspace"):
            self.assertNotIn(value, rendered)

    def test_supplied_keys_are_not_inferred_or_merged(self):
        before = snapshot()
        before["entities"]["cards"] = [{"key": "physical-old", "data": {"move": "e4"}}]
        after = copy.deepcopy(before)
        after["entities"]["cards"][0]["key"] = "physical-new"
        self.assertEqual(compare.compare_snapshots(before, after)["entities"]["cards"], {
            "missing": ["physical-old"], "extra": ["physical-new"], "changed": [],
        })

    def test_fen_text_is_not_normalized(self):
        before = snapshot()
        before["entities"]["cards"] = [{"key": "card-1", "data": {"fen": "synthetic w - - 0 1"}}]
        after = copy.deepcopy(before)
        after["entities"]["cards"][0]["data"]["fen"] = "synthetic w - - 1 2"
        self.assertEqual(compare.compare_snapshots(before, after)["entities"]["cards"]["changed"], [
            {"key": "card-1", "paths": ["/fen"]},
        ])

    def test_boolean_integer_and_other_value_types_are_distinct(self):
        for old, new in ((True, 1), (False, 0), (1, 1.0), (1, "1"), (None, "null"), ([], {})):
            with self.subTest(old=old, new=new):
                before = snapshot()
                before["entities"]["cards"] = [{"key": "card-1", "data": {"value": old}}]
                after = copy.deepcopy(before)
                after["entities"]["cards"][0]["data"]["value"] = new
                self.assertEqual(compare.compare_snapshots(before, after)["entities"]["cards"]["changed"], [
                    {"key": "card-1", "paths": ["/value"]},
                ])

    def test_array_order_length_and_nested_value_types_are_preserved(self):
        before = snapshot()
        before["entities"]["browser_intents"] = [{"key": "intent-1", "data": {"items": [1, True, "x"]}}]
        after = copy.deepcopy(before)
        after["entities"]["browser_intents"][0]["data"]["items"] = [True, 1]
        self.assertEqual(compare.compare_snapshots(before, after)["entities"]["browser_intents"]["changed"], [
            {"key": "intent-1", "paths": ["/items/0", "/items/1", "/items/2"]},
        ])

    def test_added_removed_and_escaped_field_paths(self):
        before = snapshot()
        before["entities"]["cards"] = [{"key": "card-1", "data": {"a/b": {"~": 1}, "removed": None}}]
        after = copy.deepcopy(before)
        after["entities"]["cards"][0]["data"] = {"a/b": {"~": 2}, "": [None]}
        self.assertEqual(compare.compare_snapshots(before, after)["entities"]["cards"]["changed"], [
            {"key": "card-1", "paths": ["/", "/a~1b/~0", "/removed"]},
        ])

    def test_output_is_deterministic_with_sorted_keys_and_paths(self):
        before = snapshot()
        before["entities"]["cards"] = [
            {"key": key, "data": {"z": 1, "a": 1}} for key in ("z", "b", "a", "y")
        ]
        after = copy.deepcopy(before)
        after["entities"]["cards"] = after["entities"]["cards"][1:3]
        for row in after["entities"]["cards"]:
            row["data"] = {"a": 2, "z": 2}
        after["entities"]["cards"].extend({"key": key, "data": {}} for key in ("x", "c"))
        result = compare.compare_snapshots(before, after)
        self.assertEqual(result["entities"]["cards"], {
            "missing": ["y", "z"], "extra": ["c", "x"],
            "changed": [{"key": key, "paths": ["/a", "/z"]} for key in ("a", "b")],
        })
        before["entities"]["cards"].reverse()
        after["entities"]["cards"].reverse()
        self.assertEqual(json.dumps(result), json.dumps(compare.compare_snapshots(before, after)))

    def test_duplicate_record_keys_are_invalid_in_either_input(self):
        invalid = snapshot()
        invalid["entities"]["reviews"] = [{"key": "duplicate", "data": {}}] * 2
        for before, after in ((invalid, snapshot()), (snapshot(), invalid)):
            with self.subTest(before_invalid=before is invalid):
                with self.assertRaises(compare.SnapshotError):
                    compare.compare_snapshots(before, after)

    def test_empty_groups_are_valid_but_each_missing_group_is_invalid(self):
        self.assertTrue(compare.compare_snapshots(snapshot(), snapshot())["equal"])
        for group in GROUPS:
            with self.subTest(group=group):
                invalid = snapshot()
                del invalid["entities"][group]
                with self.assertRaises(compare.SnapshotError):
                    compare.compare_snapshots(snapshot(), invalid)

    def test_workspace_mismatch_is_invalid(self):
        after = snapshot()
        after["workspace_id"] = "other-synthetic-workspace"
        with self.assertRaises(compare.SnapshotError):
            compare.compare_snapshots(snapshot(), after)

    def test_unresolved_mapping_is_invalid(self):
        after = snapshot()
        after["unresolved"] = [{"mapping": "synthetic-unresolved"}]
        with self.assertRaises(compare.SnapshotError):
            compare.compare_snapshots(snapshot(), after)

    def test_top_level_fields_versions_and_types_are_strict(self):
        invalid_documents = [None, [], "snapshot", {}, {**snapshot(), "unknown": None}]
        for field in ("schema_version", "workspace_id", "unresolved", "entities"):
            document = snapshot()
            del document[field]
            invalid_documents.append(document)
        for field, values in {
            "schema_version": (0, 2, True, 1.0, "1", None),
            "workspace_id": ("", 1, True, None, []),
            "unresolved": (None, {}, "", False, [None]),
            "entities": (None, [], "", False),
        }.items():
            invalid_documents.extend({**snapshot(), field: value} for value in values)
        for index, document in enumerate(invalid_documents):
            with self.subTest(index=index):
                with self.assertRaises(compare.SnapshotError):
                    compare.compare_snapshots(document, snapshot())

    def test_unknown_groups_wrong_group_types_and_record_shapes_are_invalid(self):
        invalid = snapshot()
        invalid["entities"]["unknown"] = []
        invalid_documents = [invalid]
        for rows in (None, {}, "", False):
            document = snapshot()
            document["entities"]["cards"] = rows
            invalid_documents.append(document)
        for row in (None, [], {}, {"key": "k"}, {"data": {}},
                    {"key": "k", "data": {}, "unknown": 1}):
            document = snapshot()
            document["entities"]["cards"] = [row]
            invalid_documents.append(document)
        for document in invalid_documents:
            with self.subTest(document=document):
                with self.assertRaises(compare.SnapshotError):
                    compare.compare_snapshots(snapshot(), document)

    def test_invalid_record_keys_and_data_types(self):
        for field, values in {
            "key": ("", None, 1, True, [], {}),
            "data": (None, [], 1, True, "{}"),
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    document = snapshot()
                    document["entities"]["cards"] = [{"key": "k", "data": {}, field: value}]
                    with self.assertRaises(compare.SnapshotError):
                        compare.compare_snapshots(snapshot(), document)

    def test_nonfinite_and_non_json_nested_values_are_invalid(self):
        for value in (float("nan"), float("inf"), float("-inf"), (1,), {1}, b"bytes", {1: "key"}):
            with self.subTest(value=value):
                document = snapshot()
                document["entities"]["cards"] = [{"key": "k", "data": {"nested": [value]}}]
                with self.assertRaises(compare.SnapshotError):
                    compare.compare_snapshots(document, document)

    def run_cli(self, before_text, after_text):
        documents = {"before.json": before_text, "after.json": after_text}
        output, errors = io.StringIO(), io.StringIO()

        def read_input(path, **kwargs):
            self.assertEqual(kwargs, {"encoding": "utf-8"})
            return io.StringIO(documents[path])

        with patch("builtins.open", side_effect=read_input) as opened, \
                patch("sys.stdout", output), patch("sys.stderr", errors):
            exit_code = compare.main(["before.json", "after.json"])
        return exit_code, output.getvalue(), errors.getvalue(), opened

    def test_cli_equal_and_different_exit_codes_and_reads(self):
        before = populated_snapshot()
        after = copy.deepcopy(before)
        for expected_code in (0, 1):
            with self.subTest(expected_code=expected_code):
                code, output, errors, opened = self.run_cli(json.dumps(before), json.dumps(after))
                self.assertEqual(code, expected_code)
                self.assertEqual(json.loads(output), compare.compare_snapshots(before, after))
                self.assertEqual(errors, "")
                self.assertEqual([call.args for call in opened.call_args_list], [("before.json",), ("after.json",)])
            after["entities"]["reviews"].pop()

    def test_cli_rejects_malformed_nonfinite_and_duplicate_member_json(self):
        valid = json.dumps(snapshot())
        invalid_texts = ["{", valid + " trailing", valid.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1')]
        for payload in ("NaN", "Infinity", "-Infinity", "1e999", '{"a": 1, "a": 2}'):
            document = snapshot()
            document["entities"]["cards"] = [{"key": "k", "data": {"value": "REPLACE"}}]
            invalid_texts.append(json.dumps(document).replace('"REPLACE"', payload))
        for invalid_text in invalid_texts:
            with self.subTest(invalid_text=invalid_text):
                code, output, errors, _ = self.run_cli(valid, invalid_text)
                self.assertEqual(code, 2)
                self.assertEqual(output, "")
                self.assertEqual(set(json.loads(errors)), {"error"})
                self.assertNotIn("REPLACE", errors)

    def test_cli_schema_failures_are_invalid_and_do_not_leak_values(self):
        before = snapshot()
        after = snapshot()
        after["workspace_id"] = "synthetic-secret-workspace"
        code, output, errors, _ = self.run_cli(json.dumps(before), json.dumps(after))
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertNotIn("synthetic-secret-workspace", errors)
        self.assertNotIn("synthetic-workspace", errors)

    def test_cli_file_and_encoding_errors_are_invalid_without_details(self):
        for error in (OSError("synthetic-secret-path"), UnicodeError("synthetic-secret-bytes")):
            with self.subTest(error=type(error).__name__):
                output, errors = io.StringIO(), io.StringIO()
                with patch("builtins.open", side_effect=error), patch("sys.stdout", output), patch("sys.stderr", errors):
                    self.assertEqual(compare.main(["before.json", "after.json"]), 2)
                self.assertEqual(output.getvalue(), "")
                self.assertNotIn("synthetic-secret", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
