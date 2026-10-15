"""Scheduling decisions over application-owned persistence capabilities.

The caller owns the transaction, completion clock, and source identity. This
module neither acquires infrastructure nor performs queue/progression follow-ups.
"""

from __future__ import annotations

from datetime import datetime
from .clock import date
import json

from .scheduler import schedule_review, unlock_ready

# Explicit compatibility re-exports preserve the original public imports.
from .scheduling_review_contracts import (
    CardState as CardState,
    ReviewRecord as ReviewRecord,
    OpeningSeedFacts as OpeningSeedFacts,
    MaturityFacts as MaturityFacts,
    UpdatedCardState as UpdatedCardState,
    SchedulingReviewResult as SchedulingReviewResult,
    SchedulingReviewStore as SchedulingReviewStore,
)


def apply_scheduling_review(
    store: SchedulingReviewStore,
    card_id: str,
    outcome: str,
    *,
    guided: bool,
    source_kind: str,
    source_ref: str | None,
    light_first_interval_days: int,
    reviewed_at: datetime,
    review_day: date,
    schedule_review_function=schedule_review,
    unlock_ready_function=unlock_ready,
) -> SchedulingReviewResult:
    if source_ref and store.source_review_exists(source_kind, source_ref):
        card = store.load_card_state(card_id, require_active=False)
        if card is None:
            # The compatible wrapper historically indexes an absent replay row.
            raise TypeError("'NoneType' object is not subscriptable")
        return {
            "card_id": card_id,
            "next_due": card.due_date,
            "interval_days": card.interval_days,
            "state": card.state,
            "requeue_today": True,
            "requeue_after_cards": 4,
            "stability": card.stability,
            "scheduling_mode": card.scheduling_mode,
            "hard_correct_streak": card.hard_correct_streak,
            "suggest_shorter_prefix": False,
            "idempotent": True,
        }
    card = store.load_card_state(card_id, require_active=True)
    if card is None:
        raise KeyError("Card not found")
    schedule = schedule_review_function(
        outcome,
        interval_days=card.interval_days,
        fsrs_card_json=card.fsrs_card_json,
        first_correct_at=card.first_correct_at,
        reinforcement_pending=card.reinforcement_pending,
        scheduling_mode=card.scheduling_mode,
        hard_correct_streak=card.hard_correct_streak,
        recent_attempts=json.loads(card.recent_attempts_json or "[]"),
        light_first_interval_days=light_first_interval_days,
        reviewed_at=reviewed_at,
        review_day=review_day,
    )
    store.append_review(
        ReviewRecord(
            card_id,
            outcome,
            schedule.internal_rating,
            guided,
            reviewed_at,
            card.interval_days,
            schedule.interval_days,
            source_kind,
            source_ref,
        )
    )
    maturity = store.load_maturity_facts(card_id)
    successful_days = maturity.successful_review_days
    recent_outcomes = list(maturity.recent_outcomes)
    if maturity.opening_seed is not None:
        successful_days += maturity.opening_seed.baseline_successful_days
        recent_outcomes.extend(
            ["correct"]
            * min(
                maturity.opening_seed.baseline_recent_clean,
                max(0, 2 - len(recent_outcomes)),
            )
        )
    state = (
        "mature"
        if unlock_ready_function(schedule.stability, successful_days, recent_outcomes)
        else "learning"
    )
    store.save_card_state(
        card_id,
        UpdatedCardState(
            schedule.due_date.isoformat(),
            schedule.interval_days,
            schedule.fsrs_card_json,
            schedule.first_correct_at,
            schedule.reinforcement_pending,
            schedule.stability,
            guided,
            state,
            schedule.scheduling_mode,
            schedule.hard_correct_streak,
            schedule.recent_attempts,
        ),
    )
    return {
        "card_id": card_id,
        "next_due": schedule.due_date.isoformat(),
        "interval_days": schedule.interval_days,
        "state": state,
        "requeue_today": schedule.requeue_today,
        "requeue_after_cards": schedule.requeue_after_cards,
        "stability": schedule.stability,
        "scheduling_mode": schedule.scheduling_mode,
        "hard_correct_streak": schedule.hard_correct_streak,
        "suggest_shorter_prefix": schedule.suggest_shorter_prefix,
        "idempotent": False,
    }
