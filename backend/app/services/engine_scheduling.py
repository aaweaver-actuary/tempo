"""Persist engine selection debt with accepted leases, never with engine work."""

from fastapi import HTTPException

from tempo_legacy_database.adapters.scheduling import engine as storage
from tempo_legacy_database.adapters.scheduling.engine import (
    ELIGIBLE_THREAT_REQUEST_SQL, INTERACTIVE_REQUEST_FILTER_SQL,
    ORDINARY_GAME_ELIGIBILITY_SQL, ORDINARY_GAME_ORDER_SQL,
    execute_scheduling_sql, record_automated_selection, record_ordinary_selection,
    interactive_request_sql,
)


def lock_engine_scheduling_state(database):
    """Serialize discretionary claims before request/game locks; yield when busy."""
    scheduling_row = storage.scheduling_row(database)
    if scheduling_row is not None:
        return int(scheduling_row[0])
    if storage.scheduling_state_exists(database):
        return None
    raise HTTPException(503, 'Engine scheduling state is unavailable; restore the database and retry')


def reserved_ordinary_game(database, automated_streak, now):
    if automated_streak < 3:
        return None
    ordinary_game = storage.ordinary_game(database, now)
    return ordinary_game[0] if ordinary_game else None


def ordinary_selection_allowed(database):
    """An existing receipt replays before this guard; new game claims yield to attempts."""
    if lock_engine_scheduling_state(database) is None:
        return False
    # The storage predicate retains the defensive setting and shared recommendation rules.
    return storage.interactive_request(database) is None
