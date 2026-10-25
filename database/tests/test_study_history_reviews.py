"""Synthetic-only preparation proofs; no host, PostgreSQL or replay acceptance."""

import copy
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "study_history_reviews", ROOT / "tooling" / "study_history_reviews.py")
reviews = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reviews)
FIXTURES = ROOT / "tests" / "fixtures" / "study_history_reviews"


def synthetic_source():
    return json.loads((FIXTURES / "synthetic-source.json").read_text())


def row(source, table):
    return source["tables"][table][0]["row"]


def record(output, group, table):
    return next(record["data"] for record in output[group]
                if record["key"] == f"synthetic:{table}:0")


class ReviewHistoryPreparationTests(unittest.TestCase):
    def convert(self, source=None, approval=lambda packet: True):
        return reviews.convert_reviews(synthetic_source() if source is None else source, approval=approval)

    def rejects(self, source, pattern):
        with self.assertRaisesRegex(reviews.ReviewConversionError, pattern):
            self.convert(source)

    def test_synthetic_fixture_has_exact_expected_projection(self):
        expected = json.loads((FIXTURES / "synthetic-expected.json").read_text())
        self.assertEqual(self.convert(), expected)

    def test_repeated_projection_is_deterministic_without_source_mutation(self):
        source = synthetic_source()
        original = copy.deepcopy(source)
        first = self.convert(source)
        self.assertEqual(first, self.convert(source))
        self.assertEqual(source, original)
        record(first, "schedules", "cards")["cards"]["due_date"] = "synthetic-changed"
        self.assertEqual(source, original)
        self.assertNotEqual(first, self.convert(source))

    def test_approval_must_return_exactly_true(self):
        for answer in (False, None, 1, "approved", {}):
            with self.subTest(answer=answer):
                with self.assertRaisesRegex(reviews.ReviewConversionError, "approval"):
                    self.convert(approval=lambda packet: answer)

    def test_approval_receives_whole_packet_including_identity_and_rows(self):
        source = synthetic_source()
        packets = []
        self.convert(source, approval=lambda packet: packets.append(packet) or True)
        self.assertEqual(packets, [source])
        packets[0]["tables"].clear()
        self.assertEqual(source, synthetic_source())

    def test_approval_cannot_mutate_authorized_local_facts(self):
        source = synthetic_source()
        def approve(packet):
            row(packet, "cards")["due_date"] = "synthetic-approval-copy"
            row(source, "cards")["due_date"] = "synthetic-external-copy"
            return True
        result = self.convert(source, approve)
        self.assertEqual(record(result, "schedules", "cards")["cards"]["due_date"], "2030-01-09")

    def test_approval_failure_does_not_leak_private_reason(self):
        def fail(packet):
            raise RuntimeError("synthetic-private-approval-detail")
        with self.assertRaises(reviews.ReviewConversionError) as error:
            self.convert(approval=fail)
        self.assertNotIn("synthetic-private-approval-detail", str(error.exception))

    def test_missing_or_unresolved_authority_fails_closed(self):
        for authority in (None, "", {}, [], False, 3):
            source = synthetic_source()
            source["authority"] = authority
            self.rejects(source, "authority")
        source = synthetic_source()
        source["unresolved"] = ["synthetic-unapproved-card-alias"]
        self.rejects(source, "unresolved")

    def test_exact_packet_and_version_are_required(self):
        for transform in (
            lambda source: source.update(schema_version=True),
            lambda source: source.update(schema_version=2),
            lambda source: source.update(unexpected="synthetic"),
            lambda source: source.pop("unresolved"),
        ):
            source = synthetic_source()
            transform(source)
            self.rejects(source, "packet|schema_version")

    def test_missing_and_unrecognized_tables_cannot_disappear_silently(self):
        source = synthetic_source()
        del source["tables"]["tactic_introductions"]
        self.rejects(source, "table set")
        source = synthetic_source()
        source["tables"]["teaching_states"] = []
        self.rejects(source, "table set")

    def test_missing_or_new_captured_column_requires_mapping(self):
        for table in reviews.TABLE_COLUMNS:
            if table == "frozen_opening_pending":
                continue
            for extra in (False, True):
                with self.subTest(table=table, extra=extra):
                    source = synthetic_source()
                    if extra:
                        row(source, table)["synthetic_unclassified_column"] = "synthetic-private-value"
                    else:
                        row(source, table).pop(next(iter(row(source, table))))
                    self.rejects(source, "complete source columns")

    def test_supplied_keys_are_required_and_duplicates_fail(self):
        source = synthetic_source()
        source["tables"]["reviews"][0]["key"] = ""
        self.rejects(source, "opaque key")
        source = synthetic_source()
        source["tables"]["reviews"][0]["key"] = source["tables"]["opening_evidence_summaries"][0]["key"]
        self.rejects(source, "duplicate approved record key")

    def test_duplicate_physical_primary_key_cannot_duplicate_history(self):
        source = synthetic_source()
        duplicate = copy.deepcopy(source["tables"]["reviews"][0])
        duplicate["key"] = "synthetic-different-comparison-key"
        source["tables"]["reviews"].append(duplicate)
        self.rejects(source, "duplicate complete primary key")

    def test_review_retry_source_identity_cannot_settle_twice(self):
        source = synthetic_source()
        duplicate = copy.deepcopy(source["tables"]["reviews"][0])
        duplicate["key"] = "synthetic-other-review"
        duplicate["row"]["id"] = 102
        source["tables"]["reviews"].append(duplicate)
        self.rejects(source, "duplicate original review source identity")

    def test_review_values_and_invalidation_are_retained_without_regrading(self):
        source = synthetic_source()
        actual = record(self.convert(source), "reviews", "reviews")["reviews"]
        self.assertEqual(actual, row(source, "reviews"))
        self.assertEqual(actual["rating"], "correct")
        self.assertEqual(actual["internal_rating"], "good")
        self.assertIsNotNone(actual["invalidated_at"])
        self.assertIs(type(actual["guided"]), int)

    def test_fsrs_and_snapshot_text_are_preserved_without_scheduler_replay(self):
        source = synthetic_source()
        result = self.convert(source)
        actual = record(result, "schedules", "cards")["cards"]
        self.assertEqual(actual["fsrs_card_json"], row(source, "cards")["fsrs_card_json"])
        self.assertEqual(actual["recent_attempts_json"], "[true, false, true]")
        self.assertEqual(actual["interval_days"], 7)
        snapshot = record(result, "schedules", "review_schedule_snapshots")["review_schedule_snapshots"]
        self.assertIn("unrecognized_retained", snapshot["state_json"])
        self.assertEqual(snapshot, row(source, "review_schedule_snapshots"))

    def test_timezone_and_spent_introduction_facts_do_not_default(self):
        source = synthetic_source()
        result = self.convert(source)
        settings = record(result, "schedules", "settings")
        self.assertEqual(settings["settings"]["timezone"], "Asia/Kolkata")
        self.assertEqual(settings["settings"]["tactic_new_per_day"], 4)
        self.assertNotIn("tactics_new_per_day", settings["settings"])
        self.assertEqual(settings["cold"]["settings"]["defense_new_cards_per_day"], 5)
        introduction = record(result, "introductions", "tactic_introductions")
        self.assertEqual(introduction["cold"]["tactic_introductions"], row(source, "tactic_introductions"))
        progress = record(result, "introductions", "tactic_progress")["tactic_progress"]
        self.assertIsNone(progress["card_id"])
        self.assertIsNotNone(progress["clean_pass_at"])
        self.assertIsNone(progress["admitted_at"])
        row(source, "settings")["timezone"] = "local"
        self.rejects(source, "effective timezone")

    def test_frozen_order_preserves_memberships_fingerprint_and_cold_run(self):
        source = synthetic_source()
        result = self.convert(source)
        rank = record(result, "accepted_definitions", "frozen_opening_ranks")["frozen_opening_ranks"]
        self.assertEqual(rank, row(source, "frozen_opening_ranks"))
        self.assertEqual(rank["ordinal"], 3)
        self.assertEqual(rank["memberships_json"], '[ "synthetic-repertoire", "synthetic-shared" ]')
        run = record(result, "accepted_definitions", "frozen_opening_runs")
        self.assertEqual(run["cold"]["frozen_opening_runs"]["plan_json"], '{ "retained": true }')
        self.assertEqual(run["frozen_opening_runs"]["reader_version"], "synthetic-reader-1")

    def test_pending_frozen_apply_requires_its_original_owner(self):
        source = synthetic_source()
        source["tables"]["frozen_opening_pending"] = [{
            "key": "synthetic-pending", "row": {"run_id": "synthetic-run"}}]
        self.rejects(source, "reconcile pending apply")

    def test_frozen_ordinals_and_run_references_must_be_complete(self):
        source = synthetic_source()
        row(source, "frozen_opening_ranks")["ordinal"] = 0
        self.rejects(source, "positive unique ordinal")
        source = synthetic_source()
        row(source, "frozen_opening_activation")["run_id"] = "synthetic-unknown-run"
        self.rejects(source, "frozen run identity")
        source = synthetic_source()
        row(source, "frozen_opening_runs")["previous_run_id"] = "synthetic-missing-previous"
        self.rejects(source, "frozen run identity")

    def test_unresolved_local_identity_closure_fails_closed(self):
        for table, field in (("reviews", "card_id"),
                             ("opening_card_schedule_seeds", "source_card_id"),
                             ("review_schedule_snapshots", "review_id"),
                             ("opening_evidence_observations", "decision_id")):
            with self.subTest(table=table):
                source = synthetic_source()
                row(source, table)[field] = 999 if field == "review_id" else "synthetic-unresolved"
                self.rejects(source, "identity is unresolved")

    def test_bigint_flags_and_boolean_settlements_keep_distinct_types(self):
        source = synthetic_source()
        row(source, "reviews")["guided"] = False
        self.rejects(source, "column types")
        source = synthetic_source()
        row(source, "remediation_settlements")["invalidated"] = 1
        self.rejects(source, "column types")
        source = synthetic_source()
        row(source, "cards")["stability"] = float("nan")
        self.rejects(source, "finite JSON")

    def test_empty_tables_are_explicit_and_do_not_create_new_history(self):
        source = synthetic_source()
        source["tables"] = {table: [] for table in reviews.TABLE_COLUMNS}
        self.assertEqual(self.convert(source), {
            "reviews": [], "schedules": [], "introductions": [], "accepted_definitions": []})

    def test_reordering_source_rows_does_not_change_projection(self):
        source = synthetic_source()
        other = copy.deepcopy(source["tables"]["reviews"][0])
        other["key"] = "synthetic-review-earlier-key"
        other["row"].update(id=102, source_ref=None, invalidated_at=None, invalidation_reason=None)
        source["tables"]["reviews"].append(other)
        expected = self.convert(source)
        source["tables"]["reviews"].reverse()
        self.assertEqual(self.convert(source), expected)

    def test_error_does_not_include_captured_values_or_row_keys(self):
        source = synthetic_source()
        source["tables"]["reviews"][0]["key"] = "synthetic-private-key"
        row(source, "reviews")["card_id"] = "synthetic-private-card"
        with self.assertRaises(reviews.ReviewConversionError) as error:
            self.convert(source)
        self.assertNotIn("synthetic-private", str(error.exception))


if __name__ == "__main__":
    unittest.main()
