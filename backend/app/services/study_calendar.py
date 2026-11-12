"""Capture the current Study calendar at a borrowed use-case boundary."""
from datetime import datetime, timezone
from fastapi import HTTPException
from tempo_storage.commands.queue import study_timezone
from app.study.policy.clock import StudyCalendar, study_calendar_at


def capture_study_calendar(database, *, now: datetime | None = None) -> StudyCalendar:
    observed_at = now if now is not None else datetime.now(timezone.utc)
    timezone_name = study_timezone(database)
    if timezone_name is None:
        raise HTTPException(503, "Current Study policy is unavailable")
    return study_calendar_at(observed_at, timezone_name)
