from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Schedule:
    interval_days: int
    due_date: date
    ease: float
    repetitions: int
    lapses: int
    state: str
    requeue_today: bool
    requeue_after_cards: int | None


def schedule_review(rating: str, interval_days: int, ease: float, repetitions: int, lapses: int) -> Schedule:
    """Daily-bucket scheduler with same-day reshuffling for failed cards."""
    if rating == "again":
        next_interval = 0
        ease = max(1.3, ease - 0.2)
        repetitions = 0
        lapses += 1
    elif rating == "hard":
        next_interval = max(2, round(max(1, interval_days) * 1.35))
        ease = max(1.3, ease - 0.05)
        repetitions += 1
    elif rating == "easy":
        next_interval = max(7, round(max(1, interval_days) * ease * 1.35))
        ease += 0.1
        repetitions += 1
    else:
        next_interval = max(3, round(max(1, interval_days) * ease))
        repetitions += 1

    state = "learning"
    return Schedule(
        interval_days=next_interval,
        due_date=date.today() if rating == "again" else date.today() + timedelta(days=next_interval),
        ease=ease,
        repetitions=repetitions,
        lapses=lapses,
        state=state,
        requeue_today=rating == "again",
        requeue_after_cards=4 if rating == "again" else None,
    )


def unlock_ready(interval_days: int, successful_review_days: int, recent_ratings: list[str]) -> bool:
    """Conservative descendant gate: stable over time, not just several quick wins."""
    return (
        interval_days >= 14
        and successful_review_days >= 3
        and len(recent_ratings) >= 2
        and "again" not in recent_ratings[:2]
    )
