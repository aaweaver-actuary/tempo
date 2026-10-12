"""Scheduling decisions over application-owned persistence capabilities.

The caller owns the transaction, completion clock, and source identity. This
module neither acquires infrastructure nor performs queue/progression follow-ups.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
from typing import Protocol, TypedDict

from .scheduler import schedule_review, unlock_ready


@dataclass(frozen=True)
class CardState:
    due_date: str
    interval_days: int
    fsrs_card_json: str | None
    first_correct_at: str | None
    reinforcement_pending: bool
    stability: float
    guided_review: bool
    state: str
    scheduling_mode: str
    hard_correct_streak: int
    recent_attempts_json: str | None


@dataclass(frozen=True)
class ReviewRecord:
    card_id: str
    rating: str
    internal_rating: str
    guided: bool
    reviewed_at: datetime
    previous_interval: int
    next_interval: int
    source_kind: str
    source_ref: str | None


@dataclass(frozen=True)
class OpeningSeedFacts:
    baseline_successful_days: int
    baseline_recent_clean: int


@dataclass(frozen=True)
class MaturityFacts:
    successful_review_days: int
    recent_outcomes: tuple[str, ...]
    opening_seed: OpeningSeedFacts | None


@dataclass(frozen=True)
class UpdatedCardState:
    due_date: str
    interval_days: int
    fsrs_card_json: str
    first_correct_at: str | None
    reinforcement_pending: bool
    stability: float
    guided_review: bool
    state: str
    scheduling_mode: str
    hard_correct_streak: int
    recent_attempts: tuple[str, ...]


class SchedulingReviewResult(TypedDict):
    card_id: str
    next_due: str
    interval_days: int
    state: str
    requeue_today: bool
    requeue_after_cards: int | None
    stability: float
    scheduling_mode: str
    hard_correct_streak: int
    suggest_shorter_prefix: bool
    idempotent: bool


class SchedulingReviewStore(Protocol):
    def source_review_exists(self, source_kind: str, source_ref: str) -> bool: ...
    def load_card_state(self, card_id: str, *, require_active: bool) -> CardState | None: ...
    def append_review(self, review: ReviewRecord) -> None: ...
    def load_maturity_facts(self, card_id: str) -> MaturityFacts: ...
    def save_card_state(self, card_id: str, state: UpdatedCardState) -> None: ...


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
) -> SchedulingReviewResult:
    if source_ref and store.source_review_exists(source_kind, source_ref):
        card = store.load_card_state(card_id, require_active=False)
        if card is None:
            # The compatible wrapper historically indexes an absent replay row.
            raise TypeError("'NoneType' object is not subscriptable")
        return {
            'card_id': card_id, 'next_due': card.due_date,
            'interval_days': card.interval_days, 'state': card.state,
            'requeue_today': True, 'requeue_after_cards': 4,
            'stability': card.stability, 'scheduling_mode': card.scheduling_mode,
            'hard_correct_streak': card.hard_correct_streak,
            'suggest_shorter_prefix': False, 'idempotent': True,
        }
    card = store.load_card_state(card_id, require_active=True)
    if card is None:
        raise KeyError('Card not found')
    schedule = schedule_review(
        outcome, interval_days=card.interval_days,
        fsrs_card_json=card.fsrs_card_json, first_correct_at=card.first_correct_at,
        reinforcement_pending=card.reinforcement_pending,
        scheduling_mode=card.scheduling_mode,
        hard_correct_streak=card.hard_correct_streak,
        recent_attempts=json.loads(card.recent_attempts_json or '[]'),
        light_first_interval_days=light_first_interval_days,
        reviewed_at=reviewed_at, review_day=review_day,
    )
    store.append_review(ReviewRecord(
        card_id, outcome, schedule.internal_rating, guided, reviewed_at,
        card.interval_days, schedule.interval_days, source_kind, source_ref,
    ))
    maturity = store.load_maturity_facts(card_id)
    successful_days = maturity.successful_review_days
    recent_outcomes = list(maturity.recent_outcomes)
    if maturity.opening_seed is not None:
        successful_days += maturity.opening_seed.baseline_successful_days
        recent_outcomes.extend(
            ['correct'] * min(maturity.opening_seed.baseline_recent_clean,
                              max(0, 2 - len(recent_outcomes)))
        )
    state = 'mature' if unlock_ready(schedule.stability, successful_days, recent_outcomes) else 'learning'
    store.save_card_state(card_id, UpdatedCardState(
        schedule.due_date.isoformat(), schedule.interval_days, schedule.fsrs_card_json,
        schedule.first_correct_at, schedule.reinforcement_pending, schedule.stability,
        guided, state, schedule.scheduling_mode, schedule.hard_correct_streak,
        schedule.recent_attempts,
    ))
    return {
        'card_id': card_id, 'next_due': schedule.due_date.isoformat(),
        'interval_days': schedule.interval_days, 'state': state,
        'requeue_today': schedule.requeue_today,
        'requeue_after_cards': schedule.requeue_after_cards,
        'stability': schedule.stability, 'scheduling_mode': schedule.scheduling_mode,
        'hard_correct_streak': schedule.hard_correct_streak,
        'suggest_shorter_prefix': schedule.suggest_shorter_prefix, 'idempotent': False,
    }
