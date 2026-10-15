"""Workspace calendar; callers choose the effective review instant explicitly."""

from contextvars import ContextVar
from datetime import date as CalendarDate, datetime, timezone
from zoneinfo import ZoneInfo

workspace_timezone = ContextVar("learning_timezone", default=None)


class date(CalendarDate):
    @classmethod
    def today(cls):
        zone = workspace_timezone.get()
        return (
            datetime.now(timezone.utc)
            .astimezone(ZoneInfo(zone) if zone else None)
            .date()
        )
