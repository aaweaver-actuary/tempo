"""Synthetic, legal-route policy proofs: no server, store, engine or provider."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.study.recombination.models import CleanStreak, ReviewEvent
from app.study.recombination.policy import reduce_clean_streak


REVIEW_TIME = datetime(2026, 10, 26, 8, tzinfo=timezone.utc)


def review(index, presentation_id="A", **changes):
    values = dict(
        event_id=f"{presentation_id}:event:{index}", presentation_id=presentation_id,
        presentation_revision=1, evidence_epoch="initial", occurred_at=REVIEW_TIME + timedelta(minutes=index),
        source="study", outcome="correct", attempt_id=f"{presentation_id}:attempt:{index}",
        review_id=f"{presentation_id}:review:{index}", complete=True, assistance="unassisted",
    )
    values.update(changes)
    return ReviewEvent(**values)


def streak(presentation_id="A", reviews=3):
    state = CleanStreak(presentation_id, 1, "initial")
    for index in range(reviews):
        state = reduce_clean_streak(state, review(index, presentation_id))
    return state


def test_two_of_three_does_not_qualify():
    state = streak(reviews=2)
    assert state.count == 2 and not state.eligible


def test_three_clean_reviews_qualify_each_member():
    assert streak("A").eligible and streak("B").eligible
    assert len(streak().completed_review_ids) == 3


@pytest.mark.parametrize("outcome,assistance", [
    ("wrong", "unassisted"), ("hint", "assisted"), ("reveal", "assisted"),
    ("guided", "assisted"), ("again", "unassisted"), ("correct", "assisted"),
])
def test_wrong_hint_guided_and_again_reset(outcome, assistance):
    reset = reduce_clean_streak(streak(), review(3, outcome=outcome, assistance=assistance))
    assert reset.count == 0
    correction = review(4, attempt_id="A:attempt:3", review_id="correction")
    assert reduce_clean_streak(reset, correction).count == 0


def test_partial_and_duplicate_do_not_increment():
    state = streak(reviews=2)
    partial = review(3, complete=False, review_id=None)
    assert reduce_clean_streak(state, partial).count == 2
    completed = review(4, attempt_id=partial.attempt_id)
    result = reduce_clean_streak(reduce_clean_streak(state, partial), completed)
    assert result.count == 3
    assert reduce_clean_streak(result, completed) is result
    another_event = replace(completed, event_id="transport-retry", occurred_at=REVIEW_TIME + timedelta(minutes=5))
    assert reduce_clean_streak(result, another_event).completed_review_ids == result.completed_review_ids
    another_review = replace(another_event, event_id="teaching-repeat", review_id="other-review")
    assert reduce_clean_streak(result, another_review).completed_review_ids == result.completed_review_ids
    # Same durable review cannot be counted again under a different attempt.
    empty = CleanStreak("A", 1, "initial")
    once = reduce_clean_streak(empty, completed)
    assert reduce_clean_streak(once, replace(another_event, attempt_id="other-attempt")).count == 1


def test_same_day_distinct_reviews_allowed():
    state = streak()
    assert state.count == 3
    assert state.last_event_at.date() == REVIEW_TIME.date()


def test_real_game_success_does_not_increment():
    state = streak(reviews=2)
    assert reduce_clean_streak(state, review(3, source="real_game")) is state
    assert reduce_clean_streak(state, review(4, source="real_game", outcome="wrong")) is state
    admitted = review(5, source="admitted_lapse", outcome="failure", attempt_id=None, review_id=None)
    assert reduce_clean_streak(state, admitted).count == 0


def test_unknown_historical_assistance_cannot_qualify():
    assert reduce_clean_streak(streak(reviews=2), review(3, assistance="unknown")).count == 0


def test_split_requires_fresh_streak():
    before = streak()
    fragment = CleanStreak("A", 1, "accepted-split:1")
    assert fragment.count == 0
    assert reduce_clean_streak(fragment, review(4)) is fragment
    for index in range(3):
        fragment = reduce_clean_streak(fragment, review(index, evidence_epoch="accepted-split:1"))
    assert before.eligible and fragment.eligible


def test_owner_orders_events_and_stale_revisions_do_not_resurrect():
    state = streak()
    with pytest.raises(ValueError, match="out_of_order_event"):
        reduce_clean_streak(state, review(-1))
    assert reduce_clean_streak(state, review(4, presentation_revision=2)) is state
    assert reduce_clean_streak(state, review(4, presentation_id="other")) is state
