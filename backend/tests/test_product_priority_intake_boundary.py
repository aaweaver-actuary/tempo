"""Games acceptance/replay against only the public idempotent encounter port.

No queue, canonical admission, scheduling, database or concrete Study owner is
available to this producer check. Receiving persistence/refresh is #487-owned.
"""
import pytest

from app.contracts.product_v02.learning import EncounterReceipt
from app.games.feedback.jobs import FeedbackRunner
from test_product_feedback_jobs import encounter_harness, spec


class AcceptedPriorityIntake:
    """Public port fake: durable acceptance, deliberately no availability work."""
    __slots__ = ("commands", "receipts", "lose_ack", "calls", "status_reads")

    def __init__(self, *, lose_ack=False):
        self.commands, self.receipts = {}, {}
        self.lose_ack, self.calls, self.status_reads = lose_ack, [], 0

    def accept_encounter(self, command):
        self.calls.append(command)
        key = command.key.logical_id()
        if key in self.receipts:
            assert command == self.commands[key]
            return self.receipts[key].model_copy(update={"outcome": "duplicate"})
        self.commands[key] = command
        receipt = EncounterReceipt(key=command.key,
            source_revision=command.source.source_revision,
            status="pending", outcome="accepted", owner_receipt_id="accepted-priority:" + key,
            admitted_at=None, available_by=None, applied_at=None,
            reason="priority_refresh_pending", detected_at=command.detected_at,
            source_digest=command.source.source_digest, observation=command.observation,
            observation_recorded=True, disposition=None,
            admission_status="pending", deferred_reason=None)
        self.receipts[key] = receipt
        if self.lose_ack:
            self.lose_ack = False
            raise TimeoutError("accepted_but_ack_lost")
        return receipt

    def encounter_status(self, key):
        self.status_reads += 1
        return self.receipts.get(key.logical_id())

    def effect_status(self, key):
        return self.encounter_status(key)

    def accept_lapse(self, command):
        pytest.fail("An encounter must not become an inline lapse")

    def control_effect(self, command):
        pytest.fail("Unchanged original evidence does not need a control")


def test_games_acknowledges_accepted_pending_without_queue_or_availability_poll():
    h = encounter_harness(observation="missed")
    intake = AcceptedPriorityIntake()
    h.coordinator.learning = intake
    h.store.enqueue(spec())
    assert h.runner.run_once() == "page"
    original, = h.store.deliveries.values()
    assert original.encounter is not None and original.lapse is None
    command_json = original.encounter.model_dump_json()
    assert h.runner.run_once() == "delivery"
    delivered = h.store.delivery(original.operation_id)
    assert delivered.state == "acknowledged"
    assert delivered.receipt.outcome == "accepted"
    assert delivered.receipt.status == delivered.receipt.admission_status == "pending"
    assert delivered.receipt.disposition is None
    assert delivered.receipt.applied_at is delivered.receipt.available_by is None
    assert delivered.encounter.model_dump_json() == command_json
    # Once accepted, the Games worker is done even while Study has no queue
    # availability. Refresh is an independent consumer operation, never a poll.
    h.clock.value += 60
    assert FeedbackRunner(h.coordinator).run_once() == "idle"
    assert len(intake.calls) == len(intake.commands) == 1 and intake.status_reads == 0


def test_lost_acceptance_ack_replays_exact_immutable_evidence_without_refresh():
    h = encounter_harness(observation="missed")
    intake = AcceptedPriorityIntake(lose_ack=True)
    h.coordinator.learning = intake
    h.store.enqueue(spec())
    assert h.runner.run_once() == "page"
    original, = h.store.deliveries.values()
    assert h.runner.run_once() == "delivery"
    assert h.store.delivery(original.operation_id).state == "pending"
    assert len(intake.commands) == 1
    h.clock.value += 10
    assert FeedbackRunner(h.coordinator).run_once() == "delivery"
    acknowledged = h.store.delivery(original.operation_id)
    assert acknowledged.state == "acknowledged"
    assert acknowledged.receipt.outcome == "duplicate"
    assert acknowledged.receipt.admission_status == "pending"
    assert acknowledged.encounter == original.encounter
    assert intake.calls == [original.encounter, original.encounter]
    assert len(intake.commands) == 1 and intake.status_reads == 0


def test_unavailable_priority_intake_keeps_original_outbox_retryable():
    h = encounter_harness(observation="missed")
    class UnavailableIntake:
        def accept_encounter(self, command):
            raise ConnectionError("priority intake unavailable")
    h.coordinator.learning = UnavailableIntake()
    h.store.enqueue(spec())
    assert h.runner.run_once() == "page"
    original, = h.store.deliveries.values()
    command_json = original.encounter.model_dump_json()
    assert h.runner.run_once() == "delivery"
    pending = h.store.delivery(original.operation_id)
    assert pending.state == "pending" and pending.receipt is None
    assert pending.encounter.model_dump_json() == command_json
    assert pending.error == "owner_acknowledgment_uncertain"
    intake = AcceptedPriorityIntake()
    h.coordinator.learning = intake
    h.clock.value += 10
    assert FeedbackRunner(h.coordinator).run_once() == "delivery"
    acknowledged = h.store.delivery(original.operation_id)
    assert acknowledged.state == "acknowledged"
    assert acknowledged.receipt.admission_status == "pending"
    assert intake.calls == [original.encounter]


def test_matched_new_capture_commits_source_checkpoint_without_study_effect():
    """#618/#619 replace new matched-effect emission with retained coverage."""
    h = encounter_harness(observation="matched")
    intake = AcceptedPriorityIntake()
    h.coordinator.learning = intake
    h.store.enqueue(spec())
    assert h.runner.run_once() == "page"
    source, = h.store.sources.values()
    capture, = h.store.captures.values()
    checkpoint = h.store.jobs["job"]
    assert source.normalized is not None and source.first_departure.status == "covered"
    assert capture.normalized == source.normalized and capture.first_departure == source.first_departure
    assert checkpoint.status == "complete" and checkpoint.window_complete and checkpoint.pages_committed == 1
    assert source.effects == source.encounters == () and h.store.deliveries == {}
    assert FeedbackRunner(h.coordinator).run_once() == "idle"
    assert intake.calls == [] and intake.receipts == {} and intake.status_reads == 0
