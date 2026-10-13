"""Scheduling records and five capabilities; the caller owns persistence."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, TypedDict


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
