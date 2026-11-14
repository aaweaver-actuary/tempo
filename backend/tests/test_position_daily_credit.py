"""Pure receipt/credit policy regressions; no database or scheduler claims."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from itertools import permutations

import pytest
from tempo_evidence_math.position_learning import (
    AcceptedEvidence,
    FirstResponse,
    GradeProposal,
    Hold,
    PositionDefinition,
    PositionKey,
)

from app.study.position_learning.daily_credit import (
    CurrentPositionState,
    GradeAcceptance,
    accept_grade,
)

DAY = date(2026, 11, 2)
NOW = datetime(2026, 11, 2, 10, tzinfo=UTC)
KEY = PositionKey("rep-1", "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -")
DEFINITION = PositionDefinition(KEY, "e2e4", "policy-1")


def event(
    event_id="event-1",
    *,
    attempt_id=None,
    index=0,
    grade="good",
    definition=DEFINITION,
    epoch="epoch-1",
    revision=1,
    day=DAY,
    accepted_day=DAY,
    observed_at=NOW,
    accepted_at=NOW,
    assisted=False,
    terminal=True,
    aggregate=True,
):
    response = FirstResponse(
        attempt_id or event_id,
        index,
        definition,
        "d2d4" if grade == "again" else "e2e4",
        assisted,
        observed_at,
        event_id,
    )
    evidence = AcceptedEvidence(
        first_response=response,
        saved=True,
        disposition="wrong" if grade == "again" else "expected",
        terminal_complete=terminal,
        aggregate_review_saved=aggregate,
        owner_epoch=epoch,
        source_digest="original-digest",
        accepted_at=accepted_at,
        accepted_study_day=accepted_day,
    )
    proposal = GradeProposal(
        KEY,
        definition.policy_version,
        event_id,
        day,
        epoch,
        revision,
        grade,
        "classified-by-C1",
        observed_at,
        accepted_at,
    )
    return proposal, evidence


def state(
    *,
    definition=DEFINITION,
    epoch="epoch-1",
    revision=1,
    day=DAY,
    last_transition_at=None,
):
    return CurrentPositionState(definition, epoch, revision, day, last_transition_at)


def accept(pair, current=None, receipts=()):
    proposal, evidence = pair
    return accept_grade(proposal, current or state(), receipts, evidence=evidence)


def advance(current, result):
    receipt = result.receipt
    return replace(
        current,
        revision=receipt.resulting_revision,
        last_transition_at=receipt.final.accepted_at
        if result.schedule_grade
        else current.last_transition_at,
    )


def test_two_due_tabs_only_first_good_transitions_and_second_observation_is_retained():
    current = state()
    first = accept(event("tab-1"), current)
    second = accept(
        event("tab-2", accepted_at=NOW + timedelta(minutes=1)),
        advance(current, first),
        (first.receipt,),
    )
    assert first.schedule_grade == "good"
    assert second.schedule_grade is None
    assert second.receipt.final.grade == "observe"
    assert second.receipt.final.reason == "stale_due_issuance"
    assert second.receipt.resulting_revision == 2
    assert second.receipt.evidence.first_response.attempt_id == "tab-2"


def test_good_then_new_wrong_then_second_genuine_wrong_each_get_again():
    current = state()
    receipts = ()
    results = []
    for pair in (
        event("good"),
        event("wrong-1", grade="again"),
        event("wrong-2", grade="again", terminal=False, aggregate=False),
    ):
        result = accept(pair, current, receipts)
        results.append(result)
        receipts += (result.receipt,)
        current = advance(current, result)
    assert [result.schedule_grade for result in results] == ["good", "again", "again"]
    assert current.revision == 4


def test_exact_replay_returns_original_after_day_policy_epoch_and_revision_change():
    pair = event()
    original = accept(pair)
    changed_current = state(
        definition=replace(DEFINITION, policy_version="policy-2"),
        epoch="epoch-2",
        revision=8,
        day=DAY + timedelta(days=1),
    )
    replay = accept(pair, changed_current, (original.receipt,))
    assert replay.receipt is original.receipt
    assert replay.replayed
    assert replay.schedule_grade is None


@pytest.mark.parametrize(
    "change", ["proposal", "action", "source", "context", "acceptance"]
)
def test_same_event_changed_receipt_is_conflict(change):
    proposal, evidence = event(grade="again")
    original = accept((proposal, evidence))
    if change == "proposal":
        proposal = replace(proposal, reason="different-classification")
    elif change == "action":
        evidence = replace(
            evidence, first_response=replace(evidence.first_response, actual_uci="c2c4")
        )
    elif change == "source":
        evidence = replace(evidence, source_digest="changed-digest")
    elif change == "context":
        evidence = replace(
            evidence, first_response=replace(evidence.first_response, index=1)
        )
    else:
        evidence = replace(evidence, accepted_at=NOW + timedelta(minutes=1))
    assert accept((proposal, evidence), receipts=(original.receipt,)) == Hold(
        "event_id_reused_with_changed_payload"
    )


def test_new_event_id_cannot_turn_same_original_attempt_decision_into_new_failure():
    first = accept(event("wrong", attempt_id="original-attempt", grade="again"))
    retry = accept(
        event("renamed", attempt_id="original-attempt", grade="again"),
        receipts=(first.receipt,),
    )
    assert retry == Hold("first_response_event_id_changed")


@pytest.mark.parametrize("grade", ["good", "again"])
def test_guided_evidence_is_retained_without_new_positive_or_negative_effect(grade):
    result = accept(event(grade=grade, assisted=True))
    assert result.schedule_grade is None
    assert result.receipt.final.grade == "observe"
    assert result.receipt.final.reason == "assisted_first_response_no_new_grade"
    assert result.receipt.evidence.first_response.assistance_before


@pytest.mark.parametrize("change", ["policy", "epoch", "both"])
def test_new_policy_or_owner_epoch_cannot_reset_same_position_day_good(change):
    first = accept(event("first"))
    definition = (
        replace(DEFINITION, policy_version="policy-2")
        if change != "epoch"
        else DEFINITION
    )
    epoch = "epoch-2" if change != "policy" else "epoch-1"
    second = accept(
        event("second", definition=definition, epoch=epoch, revision=2),
        state(definition=definition, epoch=epoch, revision=2),
        (first.receipt,),
    )
    assert second.receipt.final.grade == "observe"
    assert second.receipt.final.reason == "already_good_today"
    assert second.schedule_grade is None


def test_new_owner_day_can_receive_good_with_same_exact_position():
    first = accept(event())
    next_day = DAY + timedelta(days=1)
    second = accept(
        event(
            "tomorrow",
            revision=2,
            day=next_day,
            accepted_day=next_day,
            accepted_at=NOW + timedelta(days=1),
        ),
        state(revision=2, day=next_day),
        (first.receipt,),
    )
    assert second.schedule_grade == "good"


def test_stale_positive_is_observation_even_without_good_and_never_retroactive():
    result = accept(event(observed_at=NOW - timedelta(days=3)), state(revision=3))
    assert result.receipt.final.reason == "stale_due_issuance"
    assert result.schedule_grade is None
    assert result.receipt.resulting_revision == 3


def test_midnight_uses_authoritative_owner_day_not_timestamp_timezone_date():
    # UTC dates are Nov 3, but the authoritative Study day is still Nov 2.
    utc_after_midnight = datetime(2026, 11, 3, 1, tzinfo=UTC)
    result = accept(event(accepted_at=utc_after_midnight))
    assert result.schedule_grade == "good"
    next_day = DAY + timedelta(days=1)
    late_good = accept(
        event("late", accepted_day=next_day, accepted_at=utc_after_midnight),
        state(day=next_day),
    )
    assert late_good.receipt.final.reason == "accepted_after_issued_study_day"
    wrong = accept(
        event(
            "wrong",
            grade="again",
            day=next_day,
            accepted_day=next_day,
            accepted_at=utc_after_midnight,
        ),
        state(day=next_day),
    )
    assert wrong.schedule_grade == "again"
    assert wrong.receipt.final.study_day == next_day


def test_unavailable_or_mismatched_acceptance_day_context_holds():
    assert accept(event(accepted_day=DAY + timedelta(days=1))) == Hold(
        "accepted_study_day_mismatch"
    )


def test_out_of_order_occurrence_does_not_reorder_schedule_or_waive_new_wrong():
    prior = accept(event())
    later_acceptance = NOW + timedelta(hours=2)
    wrong = accept(
        event(
            "offline-wrong",
            grade="again",
            observed_at=NOW - timedelta(days=2),
            accepted_at=later_acceptance,
        ),
        advance(state(), prior),
        (prior.receipt,),
    )
    assert wrong.schedule_grade == "again"
    assert wrong.receipt.final.observed_at == NOW - timedelta(days=2)
    assert wrong.receipt.final.accepted_at == later_acceptance
    assert wrong.receipt.resulting_revision == 3


def test_backdated_acceptance_holds_instead_of_rewriting_applied_transition():
    current = state(revision=3, last_transition_at=NOW + timedelta(hours=1))
    assert accept(event(grade="again"), current) == Hold(
        "acceptance_precedes_current_transition"
    )


def test_nondue_observation_receipt_is_preserved_and_replayed():
    pair = event(grade="observe")
    result = accept(pair)
    assert result.schedule_grade is None
    assert result.receipt.proposal == pair[0]
    assert result.receipt.evidence == pair[1]
    assert result.receipt.final.grade == "observe"
    assert accept(pair, receipts=(result.receipt,)).receipt is result.receipt


@pytest.mark.parametrize("fence", ["epoch", "definition"])
def test_new_event_must_match_current_owner_and_definition(fence):
    current = (
        state(epoch="epoch-2")
        if fence == "epoch"
        else state(definition=replace(DEFINITION, policy_version="new"))
    )
    assert isinstance(accept(event(grade="again"), current), Hold)


def test_all_acceptance_orders_preserve_one_good_and_each_independent_failure_once():
    events = (
        event("tab-1"),
        event("tab-2"),
        event("wrong-1", grade="again"),
        event("wrong-2", grade="again"),
    )
    for ordered in permutations(events):
        current, receipts, effects = state(), (), []
        for pair in ordered:
            result = accept(pair, current, receipts)
            assert isinstance(result, GradeAcceptance)
            if result.schedule_grade:
                effects.append(result.schedule_grade)
            receipts += (result.receipt,)
            current = advance(current, result)
            replay = accept(pair, current, receipts)
            assert replay.replayed and replay.schedule_grade is None
        assert effects.count("good") <= 1
        assert effects.count("again") == 2
        assert current.revision == 1 + len(effects)
        assert len(receipts) == 4


def test_partial_pending_then_completed_good_same_event_has_no_receipt_conflict():
    proposal, evidence = event(grade="pending", terminal=False, aggregate=False)
    partial = accept((proposal, evidence))
    assert partial == Hold("awaiting_saved_completion")
    # The owner retains raw partial evidence, but has no accepted grade receipt.
    completed_at = NOW + timedelta(minutes=1)
    completed = accept(
        (
            replace(proposal, grade="good", accepted_at=completed_at),
            replace(
                evidence,
                terminal_complete=True,
                aggregate_review_saved=True,
                accepted_at=completed_at,
            ),
        )
    )
    assert completed.schedule_grade == "good"
    assert completed.receipt.evidence.first_response is evidence.first_response
    assert completed.receipt.proposal.event_id == proposal.event_id


def test_terminal_completion_cannot_rewrite_accepted_negative_grade_receipt():
    pair = event(grade="again", terminal=False, aggregate=False)
    accepted_negative = accept(pair)
    proposal, evidence = pair
    changed_completion = accept(
        (
            proposal,
            replace(evidence, terminal_complete=True, aggregate_review_saved=True),
        ),
        receipts=(accepted_negative.receipt,),
    )
    assert changed_completion == Hold("event_id_reused_with_changed_payload")
    replay = accept(pair, receipts=(accepted_negative.receipt,))
    assert replay.receipt is accepted_negative.receipt
    assert replay.schedule_grade is None
