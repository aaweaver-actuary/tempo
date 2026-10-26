"""Pure C01–C04 policy. The Study application owns ordering and all effects."""
from __future__ import annotations

from dataclasses import replace

from .models import CleanStreak, ReviewEvent


def reduce_clean_streak(state: CleanStreak, event: ReviewEvent) -> CleanStreak:
    """Reduce one owner-ordered fact; old content/split generations cannot count.

    The owner admits/chronologically places game lapses before calling this
    function. Raw game results never change Study eligibility. Unknown historical
    assistance is not clean evidence. Completed attempts/reviews are terminal for
    counting, while later explicit failure evidence can still reset eligibility.
    """
    if (event.presentation_id, event.presentation_revision, event.evidence_epoch) != (
        state.presentation_id, state.presentation_revision, state.evidence_epoch
    ) or event.event_id in state.seen_event_ids:
        return state
    if event.source == "real_game":
        return state
    if state.last_event_at is not None and event.occurred_at < state.last_event_at:
        raise ValueError("out_of_order_event: Study must place admitted evidence chronologically")
    next_state = replace(
        state, seen_event_ids=state.seen_event_ids | {event.event_id}, last_event_at=event.occurred_at
    )
    failed = (event.source == "admitted_lapse" or event.outcome != "correct"
              or event.assistance == "assisted")
    if failed:
        return replace(
            next_state, count=0,
            tainted_attempt_ids=state.tainted_attempt_ids | ({event.attempt_id} if event.attempt_id else set()),
            completed_attempt_ids=state.completed_attempt_ids | ({event.attempt_id} if event.complete and event.attempt_id else set()),
            completed_review_ids=state.completed_review_ids | ({event.review_id} if event.complete and event.review_id else set()),
        )
    if not event.complete:
        return next_state
    if event.attempt_id in state.completed_attempt_ids or event.review_id in state.completed_review_ids:
        return next_state
    clean = event.assistance == "unassisted" and event.attempt_id not in state.tainted_attempt_ids
    return replace(
        next_state, count=min(3, state.count + 1) if clean else 0,
        completed_attempt_ids=state.completed_attempt_ids | {event.attempt_id},
        completed_review_ids=state.completed_review_ids | {event.review_id},
    )
