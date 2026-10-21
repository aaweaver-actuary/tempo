"""Actionable readiness checks for the PostgreSQL Docker product."""

from __future__ import annotations

from datetime import date
import json

from fastapi import HTTPException

from app.celery_app import celery_app
from tempo_legacy_database.connections import read_connection
from tempo_legacy_database.adapters.postgres_readiness import read_health_facts
from tempo_legacy_database.ddl.schema_version import POSTGRES_SCHEMA_VERSION


def postgres_health() -> dict[str, object]:
    try:
        version_row, settings_row, queue_row, queue_task = read_health_facts(read_connection, lambda: date.today())
    except Exception as error:
        raise HTTPException(
            503, "PostgreSQL reader unavailable; check the database, reader role, and migrations"
        ) from error
    if version_row is None or version_row[0] != POSTGRES_SCHEMA_VERSION:
        raise HTTPException(503, f"PostgreSQL schema is not version {POSTGRES_SCHEMA_VERSION}; "
                            "stop writers, verify a backup, then apply the documented schema upgrade")
    if settings_row is None:
        raise HTTPException(503, "PostgreSQL settings are missing; verify the SQLite import")
    try:
        active_queues = celery_app.control.inspect(timeout=1.0).active_queues() or {}
    except Exception as error:
        raise HTTPException(503, "Redis or Celery control is unavailable; check the broker and workers") from error
    available_queues = {
        queue["name"] for worker_queues in active_queues.values()
        for queue in worker_queues
    }
    missing = {"foreground", "background"} - available_queues
    if missing:
        raise HTTPException(
            503, "Required Celery workers are unavailable: " + ", ".join(sorted(missing))
        )
    if queue_row is not None and queue_row["state"] == "failed":
        raise HTTPException(503, "Today's queue failed: " + (queue_row["last_error"] or "inspect the background worker"))
    queue_ready = (queue_row is not None and queue_row["state"] == "ready"
                   and not queue_row["refresh_pending"])
    queue_progressing = (
        queue_task is not None and queue_task["state"] in {"queued", "leased", "retrying"}
        and json.loads(queue_task["payload_json"]).get("queue_date") == date.today().isoformat()
    )
    if not queue_ready and not queue_progressing:
        raise HTTPException(
            503, "Today's PostgreSQL queue cannot progress; check its durable task and worker logs"
        )
    return {
        "status": "ok", "storage": "postgresql", "scheduler": "FSRS 6",
        "test_instance": False,
    }
