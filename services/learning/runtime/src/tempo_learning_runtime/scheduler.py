"""Legacy environmental defaults around the installed pure scheduling API."""

from datetime import date, datetime, timezone

from fsrs import Card
from tempo_scheduling import Schedule, schedule_review as _schedule_review, unlock_ready


def schedule_review(
    outcome: str,
    *,
    interval_days: int = 0,
    fsrs_card_json: str | None = None,
    first_correct_at: str | None = None,
    reinforcement_pending: bool = False,
    scheduling_mode: str = "normal",
    hard_correct_streak: int = 0,
    recent_attempts: list[str] | None = None,
    light_first_interval_days: int = 7,
    reviewed_at: datetime | None = None,
    review_day: date | None = None,
) -> Schedule:
    if outcome not in {"correct", "again"}:
        raise ValueError("outcome must be correct or again")
    now = reviewed_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    calendar_day = review_day or (
        now.date() if reviewed_at is not None else date.today()
    )
    # FSRS's historical identity generation and 1ms collision wait belong here.
    # Supplied states must never trigger a replacement identity.
    serialized_card = fsrs_card_json if fsrs_card_json else Card(due=now).to_json()
    return _schedule_review(
        outcome,
        interval_days=interval_days,
        fsrs_card_json=serialized_card,
        first_correct_at=first_correct_at,
        reinforcement_pending=reinforcement_pending,
        scheduling_mode=scheduling_mode,
        hard_correct_streak=hard_correct_streak,
        recent_attempts=recent_attempts,
        light_first_interval_days=light_first_interval_days,
        reviewed_at=now,
        review_day=calendar_day,
    )


__all__ = ["Schedule", "schedule_review", "unlock_ready"]
