from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from fsrs import Card, Rating, Scheduler


DESIRED_RETENTION = 0.92
MAXIMUM_INTERVAL_DAYS = 365
MAXIMUM_GROWTH = 2.5


@dataclass(frozen=True)
class Schedule:
    interval_days: int
    due_date: date
    fsrs_card_json: str
    stability: float
    state: str
    requeue_today: bool
    requeue_after_cards: int | None
    first_correct_at: str | None
    reinforcement_pending: bool
    internal_rating: str


def _scheduler() -> Scheduler:
    return Scheduler(desired_retention=DESIRED_RETENTION, learning_steps=(), relearning_steps=(), maximum_interval=MAXIMUM_INTERVAL_DAYS, enable_fuzzing=False)


def _utc_midday(day: date) -> datetime:
    return datetime.combine(day, time(hour=12), timezone.utc)


def _effective_review_time(card: Card, reviewed_at: datetime, previous_interval: int) -> datetime:
    """Keep limited evidence from lateness without letting missed days create huge jumps."""
    if card.last_review is None or previous_interval <= 0:
        return reviewed_at
    scheduled = card.last_review + timedelta(days=previous_interval)
    lateness = max(timedelta(), reviewed_at - scheduled)
    allowance = timedelta(days=max(7, round(previous_interval * 0.25)))
    return min(reviewed_at, scheduled + min(lateness, allowance))


def schedule_review(
    outcome: str,
    *,
    interval_days: int = 0,
    fsrs_card_json: str | None = None,
    first_correct_at: str | None = None,
    reinforcement_pending: bool = False,
    reviewed_at: datetime | None = None,
) -> Schedule:
    if outcome not in {"correct", "again"}:
        raise ValueError("outcome must be correct or again")
    now = reviewed_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    card = Card.from_json(fsrs_card_json) if fsrs_card_json else Card(due=now)
    rating = Rating.Good if outcome == "correct" else Rating.Again
    next_card, _ = _scheduler().review_card(card, rating, _effective_review_time(card, now, interval_days))
    proposed = max(0, (next_card.due.date() - now.date()).days)
    if interval_days > 0 and outcome == "correct":
        proposed = min(proposed, max(interval_days + 1, round(interval_days * MAXIMUM_GROWTH)))
    proposed = min(MAXIMUM_INTERVAL_DAYS, proposed)
    next_card.due = _utc_midday(now.date() + timedelta(days=proposed))

    first_clean = outcome == "correct" and first_correct_at is None
    if first_clean:
        first_correct_at = now.isoformat()
        reinforcement_pending = True
    elif outcome == "correct" and reinforcement_pending:
        reinforcement_pending = False
    requeue_today = outcome == "again" or first_clean
    interval = 0 if requeue_today else proposed
    return Schedule(
        interval_days=interval,
        due_date=now.date() if requeue_today else now.date() + timedelta(days=interval),
        fsrs_card_json=next_card.to_json(),
        stability=float(next_card.stability or 0),
        state="learning",
        requeue_today=requeue_today,
        requeue_after_cards=None if first_clean else (4 if outcome == "again" else None),
        first_correct_at=first_correct_at,
        reinforcement_pending=reinforcement_pending,
        internal_rating="good" if outcome == "correct" else "again",
    )


def unlock_ready(stability: float, successful_review_days: int, recent_outcomes: list[str]) -> bool:
    return (
        stability >= 14
        and successful_review_days >= 3
        and len(recent_outcomes) >= 2
        and "again" not in recent_outcomes[:2]
    )
