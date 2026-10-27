"""Readiness decisions over supplied facts/capabilities; no runtime selection."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
import json

from .interfaces.readiness import ReadinessSnapshot, ReadinessUnavailable


def postgres_health(*, read_snapshot: Callable[[], ReadinessSnapshot],
                    worker_queues: Callable[[], frozenset[str]],
                    today: Callable[[], date], expected_schema_version: int) -> dict[str, object]:
    """Retain existing readiness order/messages without selecting infrastructure."""
    try:
        snapshot = read_snapshot()
    except Exception as error:
        raise ReadinessUnavailable(
            "PostgreSQL reader unavailable; check the database, reader role, and migrations"
        ) from error
    if snapshot.schema_version != expected_schema_version:
        raise ReadinessUnavailable(
            f"PostgreSQL schema is not version {expected_schema_version}; "
            "stop writers, verify a backup, then apply the documented schema upgrade")
    if not snapshot.settings_present:
        raise ReadinessUnavailable("PostgreSQL settings are missing; verify the SQLite import")
    try:
        available_queues = worker_queues()
    except Exception as error:
        raise ReadinessUnavailable(
            "Redis or Celery control is unavailable; check the broker and workers") from error
    missing = {"foreground", "background"} - available_queues
    if missing:
        raise ReadinessUnavailable("Required Celery workers are unavailable: " + ", ".join(sorted(missing)))
    if snapshot.queue_state == "failed":
        raise ReadinessUnavailable("Today's queue failed: " +
            (snapshot.queue_last_error or "inspect the background worker"))
    queue_ready = snapshot.queue_state == "ready" and not snapshot.queue_refresh_pending
    queue_progressing = (
        snapshot.queue_task_state in {"queued", "leased", "retrying"}
        and json.loads(snapshot.queue_task_payload_json).get("queue_date") == today().isoformat())
    if not queue_ready and not queue_progressing:
        raise ReadinessUnavailable(
            "Today's PostgreSQL queue cannot progress; check its durable task and worker logs")
    return {"status": "ok", "storage": "postgresql", "scheduler": "FSRS 6", "test_instance": False}
