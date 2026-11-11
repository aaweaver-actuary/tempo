"""Read-only activity projection and small, durable scheduling controls."""

from __future__ import annotations

import psycopg

from datetime import datetime, timezone
import logging
from fastapi import HTTPException

from tempo_storage.connections import connection
from tempo_storage import postgres
from tempo_storage.controls.activity import storage
from tempo_storage.controls.activity.storage import SOURCES
from tempo_storage.controls.claim_sql import claimable, control_order


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def report_progress(
    source: str,
    work_id: str,
    generation_key: str,
    phase: str,
    completed_units: int | None = None,
    total_units: int | None = None,
    *,
    background: bool = True,
    lease_id: str | None = None,
) -> bool:
    if source not in SOURCES:
        raise ValueError("Unknown background work source")
    if completed_units is not None and (
        completed_units < 0 or total_units is None or total_units < completed_units
    ):
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
        storage.upsert_progress(
            database,
            (
                source,
                work_id,
                generation_key,
                phase,
                completed_units,
                total_units,
                _now(),
            ),
        )
    return True


def emit_progress(
    source: str,
    work_id: str,
    generation_key: str,
    phase: str,
    completed_units: int | None = None,
    total_units: int | None = None,
) -> None:
    """Progress must never turn a successfully committed slice into a failure."""
    try:
        report_progress(
            source, work_id, generation_key, phase, completed_units, total_units
        )
    except psycopg.OperationalError:
        logging.getLogger("tempo.background").warning(
            "Could not record progress source=%s work_id=%s",
            source,
            work_id,
            exc_info=True,
        )


def set_control(
    source: str,
    work_id: str,
    action: str,
    *,
    allow_settings_blocked_resume: bool = False,
) -> bool:
    if source not in SOURCES or action not in {
        "pause",
        "resume",
        "prioritize",
        "normal",
    }:
        return False
    with connection() as database:
        return set_control_in_transaction(
            database,
            source,
            work_id,
            action,
            allow_settings_blocked_resume=allow_settings_blocked_resume,
        )


def set_control_in_transaction(
    database,
    source: str,
    work_id: str,
    action: str,
    *,
    allow_settings_blocked_resume: bool = False,
) -> bool:
    """Apply a validated activity control in the caller's short write transaction."""
    if source not in SOURCES or action not in {
        "pause",
        "resume",
        "prioritize",
        "normal",
    }:
        return False
    read_work = storage.prepare_control_read(source)
    work_row = read_work(database, work_id)
    if not work_row or work_row[0] in {"failed", "superseded"}:
        return False
    if work_row[0] == "complete":
        return False
    if action == "resume" and not allow_settings_blocked_resume and not work_row[1]:
        raise HTTPException(
            409,
            "This work is paused while Study-only mode is enabled.",
        )
    now = _now()
    storage.ensure_control(database, (source, work_id, now))
    write_control = storage.prepare_control_update(action)
    value = int(action in {"pause", "prioritize"})
    write_control(database, (value, now, source, work_id))
    return True
