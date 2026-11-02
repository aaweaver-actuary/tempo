from datetime import date, datetime, timedelta, timezone

import pytest

from app.study.position_learning.contracts import (
    AcceptedEvidence,
    DueAtIssuance,
    FirstResponse,
    Hold,
    PositionDefinition,
    PositionKey,
)
from app.study.position_learning.first_response_policy import propose_grade


STUDY_TZ = timezone(timedelta(hours=-7))
ISSUED_AT = datetime(2026, 11, 2, 9, 0, tzinfo=STUDY_TZ)
DAY = date(2026, 11, 2)
KEY = PositionKey("rep-1", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -")
DEFINITION = PositionDefinition(KEY, "e2e4", "policy-v1")


def make(due=True, *, actual="e2e4", assistance=False, disposition=None,
         terminal=True, aggregate=True, saved=True, owner_epoch="epoch-1",
         definition=DEFINITION, source_digest="original-source-digest",
         accepted_at=ISSUED_AT + timedelta(minutes=2), manual_failure=False,
         attempt_id="attempt-1", index=0, event_id="event-1", policy_day=DAY):
    response = FirstResponse(
        attempt_id=attempt_id,
        index=index,
        definition=definition,
        actual_uci=actual,
        assistance_before=assistance,
        observed_at=ISSUED_AT + timedelta(minutes=1),
        event_id=event_id,
        manual_failure=manual_failure,
    )
    evidence = AcceptedEvidence(
        first_response=response,
        saved=saved,
        disposition=disposition or ("manual_failure" if manual_failure else
                                    "expected" if actual == definition.expected_uci else "wrong"),
        terminal_complete=terminal,
        aggregate_review_saved=aggregate,
        owner_epoch=owner_epoch,
        source_digest=source_digest,
        accepted_at=accepted_at,
    )
    witness = DueAtIssuance(
        definition=DEFINITION,
        due=due,
        study_day=policy_day,
        owner_epoch="epoch-1",
        position_revision=3,
        issued_at=ISSUED_AT,
    )
    return evidence, witness


def test_due_correct_complete_saved_response_proposes_good():
    evidence, due = make()
    proposal = propose_grade(evidence, due)
    assert proposal.grade == "good"
    assert proposal.reason == "due_clean_first_response"
    assert proposal.event_id == "event-1"
    assert proposal.issued_position_revision == 3


def test_nondue_correct_is_observation_only():
    evidence, due = make(due=False)
    proposal = propose_grade(evidence, due)
    assert (proposal.grade, proposal.reason) == ("observe", "nondue_correct_no_schedule")


def test_late_day_good_is_observation_and_never_backdated():
    evidence, due = make(accepted_at=ISSUED_AT + timedelta(days=1, minutes=1))
    proposal = propose_grade(evidence, due)
    assert (proposal.grade, proposal.reason) == ("observe", "accepted_after_issued_study_day")
    assert proposal.study_day == DAY


@pytest.mark.parametrize("due", [True, False])
def test_unassisted_wrong_proposes_again_even_if_incomplete_or_nondue(due):
    evidence, witness = make(due, actual="d2d4", terminal=False, aggregate=False)
    proposal = propose_grade(evidence, witness)
    assert proposal.grade == "again"
    assert proposal.reason == "actual_first_wrong_or_manual_failure"


def test_assisted_wrong_is_not_a_new_independent_again():
    evidence, due = make(actual="d2d4", assistance=True)
    proposal = propose_grade(evidence, due)
    assert (proposal.grade, proposal.reason) == ("observe", "assisted_first_response_no_new_grade")


def test_correct_assisted_response_is_observation():
    evidence, due = make(assistance=True)
    proposal = propose_grade(evidence, due)
    assert proposal.grade == "observe"


def test_first_wrong_survives_abandoned_attempt_and_uses_acceptance_day():
    accepted_at = ISSUED_AT + timedelta(days=1, minutes=3)
    evidence, due = make(actual="d2d4", terminal=False, aggregate=False,
                         accepted_at=accepted_at)
    proposal = propose_grade(evidence, due)
    assert proposal.grade == "again"
    assert proposal.study_day == DAY + timedelta(days=1)


def test_manual_failure_is_again_without_a_move():
    evidence, due = make(actual=None, manual_failure=True, terminal=False,
                         aggregate=False)
    proposal = propose_grade(evidence, due)
    assert proposal.grade == "again"


def test_correct_without_saved_terminal_aggregate_remains_pending():
    evidence, due = make(terminal=True, aggregate=False)
    proposal = propose_grade(evidence, due)
    assert (proposal.grade, proposal.reason) == (
        "pending", "correct_waits_for_completed_saved_review")


@pytest.mark.parametrize("disposition", ["alternate", "unverified"])
def test_alternate_or_unverified_is_held_not_guessed_wrong(disposition):
    evidence, due = make(actual="d2d4", disposition=disposition)
    result = propose_grade(evidence, due)
    assert isinstance(result, Hold)
    assert result.reason == "first_response_not_authoritatively_classified"


def test_missing_durable_source_proof_is_held():
    evidence, due = make(source_digest="")
    result = propose_grade(evidence, due)
    assert isinstance(result, Hold)
    assert result.reason == "source_proof_unavailable"


def test_original_source_digest_is_retained_without_comparing_to_later_graph_state():
    evidence, due = make(source_digest="saved-original-receipt-digest")
    proposal = propose_grade(evidence, due)
    assert proposal.grade == "good"
    assert proposal.event_id == evidence.first_response.event_id


def test_epoch_mismatch_is_held():
    evidence, due = make(owner_epoch="new-epoch")
    result = propose_grade(evidence, due)
    assert isinstance(result, Hold)
    assert result.reason == "owner_epoch_changed"


def test_definition_mismatch_is_held():
    changed = PositionDefinition(KEY, "d2d4", "policy-v2")
    evidence, due = make(definition=changed, actual="d2d4")
    result = propose_grade(evidence, due)
    assert isinstance(result, Hold)
    assert result.reason == "response_definition_changed"


def test_disposition_conflict_is_held():
    evidence, due = make(actual="d2d4", disposition="expected")
    result = propose_grade(evidence, due)
    assert isinstance(result, Hold)
    assert result.reason == "first_response_disposition_conflict"


def test_unplayed_or_unsaved_response_cannot_be_graded():
    evidence, due = make(saved=False)
    result = propose_grade(evidence, due)
    assert isinstance(result, Hold)
    assert result.reason == "evidence_not_durable"


def test_independent_decisions_keep_their_attempt_index_and_event_identity():
    first, due = make(index=0, event_id="event-0")
    second, _ = make(index=2, event_id="event-2", attempt_id="attempt-2")
    first_proposal = propose_grade(first, due)
    second_proposal = propose_grade(second, due)
    assert first_proposal.event_id == "event-0"
    assert second_proposal.event_id == "event-2"
    assert first.first_response.index == 0
    assert second.first_response.index == 2
    assert first.first_response.attempt_id != second.first_response.attempt_id
