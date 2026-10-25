"""Checker qualification with synthetic ports, never a database/browser proof."""

import copy
import importlib.util
import json
from pathlib import Path
import re
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
TOOLING = ROOT / "database" / "tooling"
sys.path.insert(0, str(TOOLING))
try:
    SPEC = importlib.util.spec_from_file_location("history_rehearsal", TOOLING / "history_rehearsal.py")
    rehearsal = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(rehearsal)
finally:
    sys.path.pop(0)


def existing_synthetic_fixture():
    """Reuse #414's supplied example; do not invent installed field mapping."""
    document = (ROOT / "docs/architecture/study-first-reduction-2026-10-21/MIGRATION-MAP.md").read_text()
    match = re.search(r"<!-- synthetic-before -->\s*```json\s*(.*?)\s*```", document, re.S)
    return json.loads(match[1])


class SyntheticTargets:
    """In-memory test double; reconnection retention is not database durability."""

    def __init__(self, initial, imported, replayed, *, fault=None, replay_outcome="complete"):
        self.initial, self.imported, self.replayed = initial, imported, replayed
        self.fault, self.replay_outcome = fault, replay_outcome
        self.states, self.opened, self.closed, self.discarded = {}, [], [], []

    def open(self, scenario, *, fresh):
        self.opened.append((scenario, fresh))
        if fresh:
            self.states[scenario] = copy.deepcopy(self.initial)
        elif self.fault == "lost_on_reopen":
            self.states[scenario] = copy.deepcopy(self.initial)
        return SyntheticTarget(self, scenario)

    def discard(self, scenario):
        self.discarded.append(scenario)
        del self.states[scenario]


class SyntheticTarget:
    def __init__(self, owner, scenario):
        self.owner, self.scenario, self.import_count = owner, scenario, 0
        self.disposable = owner.fault != "unverified_target"

    def snapshot(self):
        return copy.deepcopy(self.owner.states[self.scenario])

    def import_history(self, payload, *, stop_at=None):
        owner = self.owner
        self.import_count += 1
        if owner.fault == "mutating_importer":
            payload["preserve"] = 1  # bool -> integer is a mutation too.
        if payload.get("conflict"):
            if owner.fault == "conflict_overwrite":
                owner.states[self.scenario]["entities"]["schedules"][0]["data"]["cards"]["due_date"] = "private-new-date"
            return "complete" if owner.fault == "ignored_conflict" else "conflict"
        if stop_at == "before-first-commit":
            if owner.fault == "rollback_leak":
                owner.states[self.scenario] = copy.deepcopy(owner.imported)
            return "interrupted"
        owner.states[self.scenario] = copy.deepcopy(owner.imported)
        if stop_at == "after-history-commit":
            return "interrupted"
        if owner.fault == "drop_review":
            owner.states[self.scenario]["entities"]["reviews"] = []
        if owner.fault == "duplicate_import" and self.import_count > 1:
            owner.states[self.scenario]["entities"]["reviews"] *= 2
        return "complete"

    def replay_pending(self):
        owner = self.owner
        owner.states[self.scenario] = copy.deepcopy(owner.replayed)
        if owner.fault == "rewrite_authority":
            intent = owner.states[self.scenario]["entities"]["browser_intents"][0]["data"]["intent"]
            intent["workspace"] = "private-other-authority"
        if owner.fault == "drop_blocked_pending":
            owner.states[self.scenario]["entities"]["browser_intents"] = []
        if owner.fault == "ack_is_complete":
            return "complete"
        return owner.replay_outcome

    def close(self):
        self.owner.closed.append(self.scenario)


class HistoryRehearsalTests(unittest.TestCase):
    def setUp(self):
        self.imported = existing_synthetic_fixture()
        self.initial = copy.deepcopy(self.imported)
        self.initial["entities"] = {group: [] for group in self.initial["entities"]}
        self.replayed = copy.deepcopy(self.imported)
        # An explicit synthetic golden transition, not a browser settlement
        # model: the actual replay adapter and approved golden remain missing.
        self.replayed["entities"]["browser_intents"][0]["data"]["intent"]["state"] = "committed"

    def run_checker(self, fault=None, *, replay_outcome="complete"):
        replayed = self.imported if replay_outcome == "blocked" else self.replayed
        targets = SyntheticTargets(self.initial, self.imported, replayed,
                                   fault=fault, replay_outcome=replay_outcome)
        result = rehearsal.rehearse_history(
            targets=targets, payload={"preserve": True},
            initial=self.initial, imported=self.imported, replayed=replayed,
            interruptions={"before-first-commit": self.initial,
                           "after-history-commit": self.imported},
            conflicts={"same-identity-changed-payload": {"conflict": True}},
            replay_outcome=replay_outcome,
        )
        return targets, result

    def test_repeat_import_restart_conflict_and_replay_check_supplied_full_goldens(self):
        targets, result = self.run_checker()
        self.assertEqual(len(result["checks"]), 7)
        self.assertFalse(result["real_data_acceptance"])
        self.assertFalse(result["cutover_authorized"])
        self.assertEqual(len(targets.discarded), 4)
        self.assertEqual(targets.states, {})
        self.assertEqual([entry for entry in targets.opened if not entry[1]],
                         [("interruption-0", False), ("interruption-1", False)])

    def test_blocked_replay_preserves_pending_without_claiming_completion(self):
        self.run_checker(replay_outcome="blocked")

    def test_dropped_review_is_detected_by_content_not_count_only(self):
        with self.assertRaisesRegex(rehearsal.RehearsalError, "first_import_mismatch"):
            self.run_checker("drop_review")

    def test_duplicate_retry_effect_is_rejected(self):
        with self.assertRaisesRegex(rehearsal.RehearsalError, "repeated_import_invalid_snapshot"):
            self.run_checker("duplicate_import")

    def test_before_commit_leak_is_detected_after_reopen(self):
        with self.assertRaisesRegex(rehearsal.RehearsalError, "interrupted_durable_state_mismatch"):
            self.run_checker("rollback_leak")

    def test_after_commit_loss_is_detected_after_reopen(self):
        with self.assertRaisesRegex(rehearsal.RehearsalError, "interrupted_durable_state_mismatch"):
            self.run_checker("lost_on_reopen")

    def test_conflict_overwrite_is_detected_even_when_rejected_outcome_is_reported(self):
        with self.assertRaisesRegex(rehearsal.RehearsalError, "conflict_preservation_mismatch"):
            self.run_checker("conflict_overwrite")

    def test_ignored_conflict_is_not_success(self):
        with self.assertRaisesRegex(rehearsal.RehearsalError, "unexpected_import_outcome"):
            self.run_checker("ignored_conflict")

    def test_original_browser_authority_rewrite_is_detected(self):
        with self.assertRaisesRegex(rehearsal.RehearsalError, "first_replay_mismatch"):
            self.run_checker("rewrite_authority")

    def test_acknowledgement_cannot_replace_blocked_outcome(self):
        with self.assertRaisesRegex(rehearsal.RehearsalError, "first_replay_unexpected_outcome"):
            self.run_checker("ack_is_complete", replay_outcome="blocked")

    def test_dropped_blocked_pending_is_detected(self):
        with self.assertRaisesRegex(rehearsal.RehearsalError, "first_replay_mismatch"):
            self.run_checker("drop_blocked_pending", replay_outcome="blocked")

    def test_importer_cannot_change_source_value_types(self):
        with self.assertRaisesRegex(rehearsal.RehearsalError, "import_mutated_payload"):
            self.run_checker("mutating_importer")

    def test_unverified_target_is_closed_without_import_or_discard(self):
        targets = SyntheticTargets(self.initial, self.imported, self.replayed, fault="unverified_target")
        with self.assertRaisesRegex(rehearsal.RehearsalError, "verified_disposable_target_required"):
            rehearsal.rehearse_history(targets=targets, payload={}, initial=self.initial,
                imported=self.imported, replayed=self.replayed,
                interruptions={"before-first-commit": self.initial}, conflicts={"conflict": {}})
        self.assertEqual(targets.closed, ["repeat-and-replay"])
        self.assertEqual(targets.discarded, [])

    def test_invalid_golden_fails_before_target_is_opened(self):
        targets = SyntheticTargets(self.initial, self.imported, self.replayed)
        invalid = copy.deepcopy(self.initial)
        invalid["unresolved"] = ["private-unresolved-identity"]
        with self.assertRaisesRegex(rehearsal.RehearsalError, "invalid_normalized_snapshot"):
            rehearsal.rehearse_history(targets=targets, payload={}, initial=invalid,
                imported=self.imported, replayed=self.replayed,
                interruptions={"before-first-commit": self.initial}, conflicts={"conflict": {}})
        self.assertEqual(targets.opened, [])

    def test_missing_scenarios_cannot_pass(self):
        targets = SyntheticTargets(self.initial, self.imported, self.replayed)
        with self.assertRaisesRegex(rehearsal.RehearsalError, "interruption_and_conflict_cases_required"):
            rehearsal.rehearse_history(targets=targets, payload={}, initial=self.initial,
                imported=self.imported, replayed=self.replayed, interruptions={}, conflicts={})
        self.assertEqual(targets.opened, [])

    def test_error_messages_do_not_include_private_values(self):
        try:
            self.run_checker("rewrite_authority")
        except rehearsal.RehearsalError as error:
            self.assertEqual(str(error), "first_replay_mismatch")
        else:
            self.fail("expected authority mismatch")


class ConverterCompositionTests(unittest.TestCase):
    def setUp(self):
        self.sources = {leaf: {"synthetic": leaf} for leaf in rehearsal.LEAF_GROUPS}
        self.golden = {
            "schema_version": 1, "workspace_id": "synthetic-workspace", "unresolved": [],
            "entities": {group: [] for group in existing_synthetic_fixture()["entities"]},
        }
        for leaf, groups in rehearsal.LEAF_GROUPS.items():
            for group in groups:
                self.golden["entities"][group].append(
                    {"key": leaf + ":" + group, "data": {"preserve": True}}
                )
        self.calls = []
        self.converters = {}
        for leaf, groups in rehearsal.LEAF_GROUPS.items():
            def converter(source, *, approval, leaf=leaf, groups=groups):
                self.assertIs(approval(source), True)
                self.calls.append(leaf)
                return {group: [{"key": leaf + ":" + group, "data": {"preserve": True}}]
                        for group in groups}
            self.converters[leaf] = converter

    def convert(self, **overrides):
        arguments = dict(golden=self.golden, sources=self.sources,
                         converters=self.converters,
                         approvals={leaf: lambda source: True for leaf in rehearsal.LEAF_GROUPS})
        arguments.update(overrides)
        return rehearsal.convert_history_snapshot(**arguments)

    def test_all_groups_assemble_repeatably_without_source_mutation(self):
        original = copy.deepcopy(self.sources)
        before, after = self.convert(), self.convert()
        self.assertTrue(rehearsal.compare_snapshots(before, after)["equal"])
        self.assertEqual(len(before["entities"]["accepted_definitions"]), 2)
        self.assertEqual(len(before["entities"]["introductions"]), 2)
        self.assertEqual(self.sources, original)

    def test_any_unapproved_whole_packet_stops_before_all_plugins(self):
        for approval in (lambda source: False, lambda source: 1, lambda source: None):
            with self.subTest(approval=approval):
                with self.assertRaisesRegex(rehearsal.RehearsalError, "approved_whole_source_required"):
                    self.convert(approvals={leaf: approval for leaf in rehearsal.LEAF_GROUPS})
        self.assertEqual(self.calls, [])

    def test_missing_plugin_fails_before_any_plugin_runs(self):
        del self.converters["browser_intents"]
        with self.assertRaisesRegex(rehearsal.RehearsalError, "all_four_converter_leaves_required"):
            self.convert()
        self.assertEqual(self.calls, [])

    def test_approval_is_bound_to_the_owning_leafs_exact_packet(self):
        approvals = {
            leaf: (lambda source, expected=copy.deepcopy(packet): source == expected)
            for leaf, packet in self.sources.items()
        }
        self.sources["content_identity"] = copy.deepcopy(self.sources["server_receipts"])
        with self.assertRaisesRegex(rehearsal.RehearsalError, "approved_whole_source_required"):
            self.convert(approvals=approvals)
        self.assertEqual(self.calls, [])

    def test_duplicate_shared_group_key_is_not_overwritten_or_inferred(self):
        original = self.converters["server_receipts"]
        def duplicate(source, *, approval):
            groups = original(source, approval=approval)
            groups["introductions"][0]["key"] = "reviews_schedules:introductions"
            return groups
        self.converters["server_receipts"] = duplicate
        with self.assertRaisesRegex(rehearsal.RehearsalError, "invalid_normalized_snapshot"):
            self.convert()

    def test_plugin_cannot_write_another_groups_output(self):
        self.converters["browser_intents"] = lambda source, approval: {"reviews": []}
        with self.assertRaisesRegex(rehearsal.RehearsalError, "converter_group_ownership_mismatch"):
            self.convert()

    def test_mutating_plugin_is_rejected_and_callers_source_is_unchanged(self):
        original = copy.deepcopy(self.sources)
        def mutator(source, *, approval):
            source["synthetic"] = "changed"
            return {"cards": [], "accepted_definitions": []}
        self.converters["content_identity"] = mutator
        with self.assertRaisesRegex(rehearsal.RehearsalError, "converter_mutated_source"):
            self.convert()
        self.assertEqual(self.sources, original)

    def test_non_json_packet_is_rejected_before_plugins(self):
        for value in (float("nan"), (1, 2), {1: "non-string-key"}):
            with self.subTest(value=value):
                self.sources["content_identity"] = value
                with self.assertRaisesRegex(rehearsal.RehearsalError, "payload_must_be_finite_json"):
                    self.convert()
        self.assertEqual(self.calls, [])

    def test_workspace_comes_only_from_supplied_golden_without_relabelling(self):
        self.golden["workspace_id"] = "synthetic-approved-golden-workspace"
        self.assertEqual(self.convert()["workspace_id"], self.golden["workspace_id"])

    def test_valid_but_changed_result_cannot_bypass_supplied_golden(self):
        self.golden["entities"]["cards"][0]["data"]["preserve"] = False
        with self.assertRaisesRegex(rehearsal.RehearsalError, "converted_golden_mismatch"):
            self.convert()


if __name__ == "__main__":
    unittest.main()
