"""Compatibility location for the sole explicit Study calendar conversion.

Callers supply an instant and configured timezone; no process-local mutable
calendar or implicit today override remains. Current composition captures once
and passes local_day separately from an original recorded review_day.
"""

from dataclasses import dataclass
from datetime import date, datetime

from tempo_learning_review import review_day_at

@dataclass(frozen=True)
class StudyCalendar:
    observed_at: datetime
    timezone_name: str
    day: date


def study_calendar_at(observed_at: datetime, timezone_name: str) -> StudyCalendar:
    """Select one day from explicit facts; composition owns clocks/settings."""
    return StudyCalendar(observed_at, timezone_name, review_day_at(observed_at, timezone_name))


__all__ = ["review_day_at", "StudyCalendar", "study_calendar_at"]
