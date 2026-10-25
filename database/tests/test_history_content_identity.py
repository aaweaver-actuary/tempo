"""Source-only synthetic contract proof, never actual-history acceptance."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "history_content_identity", _ROOT / "tooling" / "history_content_identity.py"
)
content = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(content)
_FIXTURES = _ROOT / "tests" / "fixtures" / "history_content_identity"


def source_fixture():
    return content.load_content_identity_source(_FIXTURES / "source-synthetic.json")


def synthetic_approval(_source):
    # Deliberately fake: no installed authority, snapshot or independent approval.
    return True


class ContentIdentityTests(unittest.TestCase):
    def test_synthetic_projection_matches_explicit_expected_fixture(self):
        expected = json.loads((_FIXTURES / "expected-synthetic.json").read_text())
        self.assertEqual(content.convert_content_identity(source_fixture(), approval=synthetic_approval), expected)

    def test_approval_binds_whole_packet_and_input_and_output_are_independent(self):
        source = source_fixture()
        original = copy.deepcopy(source)
        calls = []

        def approve(packet):
            calls.append(copy.deepcopy(packet))
            packet["tables"]["cards"].clear()
            return True

        result = content.convert_content_identity(source, approval=approve)
        self.assertEqual(calls, [original])
        self.assertEqual(source, original)
        result["cards"].clear()
        result["accepted_definitions"][0]["data"].clear()
        self.assertEqual(source, original)

    def test_missing_false_truthy_or_raising_approval_fails_closed(self):
        def denied(_source):
            raise RuntimeError("synthetic-private-denial-details")

        for approve in (None, lambda _: False, lambda _: 1, lambda _: "yes", lambda _: None, denied):
            with self.subTest(approval=type(approve).__name__):
                with self.assertRaises(content.ContentIdentityError) as failure:
                    content.convert_content_identity(source_fixture(), approval=approve)
                self.assertNotIn("synthetic-private-denial-details", str(failure.exception))

    def test_changed_key_or_payload_cannot_reuse_whole_packet_digest_approval(self):
        source = source_fixture()

        def digest(packet):
            return hashlib.sha256(json.dumps(packet, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

        approved_digest = digest(source)
        approve = lambda packet: digest(packet) == approved_digest
        content.convert_content_identity(source, approval=approve)
        for member in ("key", "row"):
            changed = copy.deepcopy(source)
            if member == "key":
                changed["tables"]["cards"][0]["key"] = "unapproved:opaque-key"
            else:
                changed["tables"]["cards"][0]["row"]["revision"] = 4
            with self.assertRaises(content.ContentIdentityError):
                content.convert_content_identity(changed, approval=approve)

    def test_original_physical_ids_are_not_merged_by_equal_moves_or_graph_mapping(self):
        result = content.convert_content_identity(source_fixture(), approval=synthetic_approval)
        cards = [r["data"]["cards"] for r in result["cards"] if "cards" in r["data"]]
        self.assertEqual({r["id"] for r in cards}, {"synthetic-card", "synthetic-projected-card"})
        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0]["moves_json"], cards[1]["moves_json"])
        aliases = [r for r in result["accepted_definitions"] if "opening_graph_legacy_mappings" in r["data"].get("cold", {})]
        self.assertEqual(len(aliases), 1)
        self.assertEqual(aliases[0]["data"]["cold"]["opening_graph_legacy_mappings"]["legacy_card_id"], "synthetic-card")

    def test_shared_memberships_and_each_integrity_issue_survive(self):
        result = content.convert_content_identity(source_fixture(), approval=synthetic_approval)
        memberships = [r for r in result["cards"] if "repertoire_cards" in r["data"]]
        self.assertEqual({r["data"]["repertoire_cards"]["repertoire_id"] for r in memberships},
                         {"synthetic-repertoire-a", "synthetic-repertoire-b"})
        blocks = [r for r in result["cards"] if "repertoire_integrity_card_blocks" in r["data"]]
        self.assertEqual(len(blocks), 2)
        self.assertEqual({r["data"]["cold"]["repertoire_integrity_card_blocks"]["issue_id"] for r in blocks},
                         {"synthetic-issue-1", "synthetic-issue-2"})

    def test_text_json_nulls_dates_booleans_and_float_types_are_not_normalized(self):
        result = content.convert_content_identity(source_fixture(), approval=synthetic_approval)
        by_key = {r["key"]: r["data"] for rows in result.values() for r in rows}
        card = by_key["synthetic:physical-card"]["cards"]
        self.assertEqual(card["moves_json"], '[ "e2e4", "e7e5", "g1f3" ]')
        self.assertIsNone(card["source_fen"])
        self.assertIs(type(card["archived"]), int)
        self.assertIs(by_key["synthetic:segmentation"]["cold"]["opening_segmentation_groups"]["diverse"], False)
        self.assertIs(type(by_key["synthetic:exercise"]["cold"]["study_exercises"]["point_value"]), float)
        self.assertEqual(by_key["synthetic:block-1"]["cold"]["repertoire_integrity_card_blocks"]["published_at"],
                         "2030-01-01T01:02:03+04:00")
        self.assertEqual(by_key["synthetic:accepted-revision"]["study_exercise_revisions"]["revision"], 2)
        self.assertEqual(by_key["synthetic:historical-executable"]["cold"]["card_revisions"]["revision"], 2)

    def test_required_empty_tables_cannot_be_omitted_or_replaced_by_unknown_tables(self):
        for mutate in (lambda t: t.pop("deleted_cards"), lambda t: t.update(unclassified=[])):
            source = source_fixture()
            mutate(source["tables"])
            with self.assertRaises(content.ContentIdentityError):
                content.convert_content_identity(source, approval=synthetic_approval)

    def test_unknown_missing_or_other_owner_fields_block_instead_of_disappearing(self):
        for mutate in (lambda r: r.update(unclassified="synthetic-private-value"),
                       lambda r: r.pop("source_ref"), lambda r: r.update(due_date="2030-02-01"),
                       lambda r: r.update(serving_active=1)):
            source = source_fixture()
            mutate(source["tables"]["cards"][0]["row"])
            with self.assertRaises(content.ContentIdentityError) as failure:
                content.convert_content_identity(source, approval=synthetic_approval)
            self.assertNotIn("synthetic-private-value", str(failure.exception))

    def test_unresolved_mapping_or_nonempty_tombstones_block(self):
        source = source_fixture()
        source["unresolved"] = ["synthetic-unapproved-identity"]
        with self.assertRaises(content.ContentIdentityError):
            content.convert_content_identity(source, approval=synthetic_approval)
        source = source_fixture()
        source["tables"]["deleted_cards"] = [{"key": "synthetic:tombstone", "row": {
            "card_id": "synthetic-deleted", "deleted_at": "synthetic-unapproved-parser"
        }}]
        with self.assertRaises(content.ContentIdentityError):
            content.convert_content_identity(source, approval=synthetic_approval)

    def test_duplicate_opaque_key_or_complete_primary_key_blocks(self):
        for replacement in ("key", "primary-key"):
            source = source_fixture()
            first, second = source["tables"]["repertoire_cards"]
            if replacement == "key":
                second["key"] = first["key"]
            else:
                second["row"] = copy.deepcopy(first["row"])
            with self.assertRaises(content.ContentIdentityError):
                content.convert_content_identity(source, approval=synthetic_approval)
        source = source_fixture()
        source["tables"]["repertoire_cards"][0]["key"] = source["tables"]["cards"][0]["key"]
        with self.assertRaises(content.ContentIdentityError):
            content.convert_content_identity(source, approval=synthetic_approval)

    def test_composite_keys_do_not_guess_delimiters_or_coerce_primary_key_types(self):
        source = source_fixture()
        source["tables"]["repertoire_cards"] = [
            {"key": "opaque-a", "row": {"repertoire_id": "a:b", "card_id": "c", "canonical_route_source": 0}},
            {"key": "opaque-b", "row": {"repertoire_id": "a", "card_id": "b:c", "canonical_route_source": 0}},
        ]
        result = content.convert_content_identity(source, approval=synthetic_approval)
        self.assertEqual(len([r for r in result["cards"] if "repertoire_cards" in r["data"]]), 2)
        source["tables"]["repertoire_cards"][0]["row"]["card_id"] = 1
        with self.assertRaises(content.ContentIdentityError):
            content.convert_content_identity(source, approval=synthetic_approval)
        source = source_fixture()
        source["tables"]["study_exercise_revisions"][0]["row"]["revision"] = "2"
        with self.assertRaises(content.ContentIdentityError):
            content.convert_content_identity(source, approval=synthetic_approval)

    def test_owner_constraint_and_invalid_primary_key_fail_closed(self):
        for field, value in (("repertoire_id", None), ("study_exercise_id", "synthetic-other-owner"),
                             ("id", True), ("id", ""), ("id", None)):
            source = source_fixture()
            source["tables"]["cards"][0]["row"][field] = value
            with self.assertRaises(content.ContentIdentityError):
                content.convert_content_identity(source, approval=synthetic_approval)

    def test_repeat_and_reorder_are_deterministic_and_do_not_mutate_source(self):
        source = source_fixture()
        original = copy.deepcopy(source)
        first = content.convert_content_identity(source, approval=synthetic_approval)
        for rows in source["tables"].values():
            rows.reverse()
        self.assertEqual(first, content.convert_content_identity(source, approval=synthetic_approval))
        for rows in source["tables"].values():
            rows.reverse()
        self.assertEqual(source, original)

    def test_nonfinite_numbers_duplicate_json_members_and_unreadable_files_reject_safely(self):
        source = source_fixture()
        source["tables"]["study_exercises"][0]["row"]["point_value"] = float("nan")
        with self.assertRaises(content.ContentIdentityError):
            content.convert_content_identity(source, approval=synthetic_approval)
        with tempfile.TemporaryDirectory(prefix="tempo-content-synthetic-") as folder:
            path = Path(folder) / "synthetic-private-path.json"
            for text in ('{"key":1,"key":2}', '{"key":NaN}', '{"key":Infinity}', 'broken'):
                path.write_text(text)
                with self.assertRaises(content.ContentIdentityError) as failure:
                    content.load_content_identity_source(path)
                self.assertNotIn(str(path), str(failure.exception))
            with self.assertRaises(content.ContentIdentityError):
                content.load_content_identity_source(Path(folder) / "absent")


if __name__ == "__main__":
    unittest.main()
