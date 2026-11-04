"""Read-only activity projection and small, durable scheduling controls."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import sqlite3
from fastapi import HTTPException

from tempo_legacy_database.connections import connection
from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.controls.activity import storage
from tempo_legacy_database.adapters.controls.activity.storage import SOURCES
from tempo_legacy_database.adapters.controls.claim_sql import claimable, control_order


from .defensive_analysis import DEFENSIVE_TASK_KINDS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def report_progress(
    source: str, work_id: str, generation_key: str, phase: str,
    completed_units: int | None = None, total_units: int | None = None,
    *, background: bool = True, lease_id: str | None = None,
) -> bool:
    if source not in SOURCES:
        raise ValueError("Unknown background work source")
    if completed_units is not None and (completed_units < 0 or total_units is None or total_units < completed_units):
        raise ValueError("Invalid progress")
    with connection(background=background) as database:
        columns, read_current = storage.prepare_progress_read(source)
        if columns:
            current = read_current(database, work_id)
            if not current:
                return False
            current_generation = ":".join(str(current[column]) for column in columns)
            if current_generation != generation_key:
                return False
        storage.upsert_progress(database, (source, work_id, generation_key, phase, completed_units, total_units, _now()))
    return True


def emit_progress(
    source: str, work_id: str, generation_key: str, phase: str,
    completed_units: int | None = None, total_units: int | None = None,
) -> None:
    """Progress must never turn a successfully committed slice into a failure."""
    try:
        report_progress(source, work_id, generation_key, phase, completed_units, total_units)
    except sqlite3.OperationalError:
        logging.getLogger("tempo.background").warning(
            "Could not record progress source=%s work_id=%s", source, work_id,
            exc_info=True,
        )


def set_control(source: str, work_id: str, action: str, *, allow_settings_blocked_resume: bool = False) -> bool:
    if source not in SOURCES or action not in {"pause", "resume", "prioritize", "normal"}:
        return False
    with connection() as database:
        return set_control_in_transaction(database, source, work_id, action,
                                          allow_settings_blocked_resume=allow_settings_blocked_resume)


def set_control_in_transaction(database, source: str, work_id: str, action: str,
                               *, allow_settings_blocked_resume: bool = False) -> bool:
    """Apply a validated activity control in the caller's short write transaction."""
    if source not in SOURCES or action not in {"pause", "resume", "prioritize", "normal"}:
        return False
    read_work = storage.prepare_control_read(source, DEFENSIVE_TASK_KINDS)
    work_row = read_work(database, work_id)
    if not work_row or work_row[0] in {"failed", "superseded"}:
        return False
    if work_row[0] == "complete":
        if source != "coverage" or not storage.unfinished_coverage(database, (work_id,)):
            return False
    if action == 'resume' and not allow_settings_blocked_resume and not work_row[1]:
        raise HTTPException(409, 'Defensive analysis is disabled in Settings. '
                            'Enable Defensive analysis in Settings before resuming this work.')
    now = _now()
    storage.ensure_control(database, (source, work_id, now))
    write_control = storage.prepare_control_update(action)
    value = int(action in {"pause", "prioritize"})
    write_control(database, (value, now, source, work_id))
    if source == "coverage" and action == "pause":
        storage.release_maia_lease(database, (now, work_id))
    if source == "threat_analysis" and action == "pause":
        storage.release_threat_lease(database, (now, work_id))
    return True
