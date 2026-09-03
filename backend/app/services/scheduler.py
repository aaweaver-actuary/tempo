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


def schedule_review(rating: str, interval_days: int, ease: float, repetitions: int, lapses: int) -> Schedule:
    """Provisional daily-bucket scheduler; exact policy is a product decision."""
    if rating == "again":
        next_interval = 1
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

    state = "mature" if repetitions >= 4 and next_interval >= 21 else "learning"
    return Schedule(
        interval_days=next_interval,
        due_date=date.today() + timedelta(days=next_interval),
        ease=ease,
        repetitions=repetitions,
        lapses=lapses,
        state=state,
    )
