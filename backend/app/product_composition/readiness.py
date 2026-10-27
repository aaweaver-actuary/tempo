"""Concrete operational observation wiring; API composition owns these clients."""
from datetime import date

from fastapi import HTTPException

from app.celery_app import celery_app
from app.operations.interfaces import ReadinessSnapshot, ReadinessUnavailable, evaluate_readiness
from tempo_legacy_database.connections import read_connection
from tempo_legacy_database.adapters.postgres_readiness import read_health_facts
from tempo_legacy_database.ddl.schema_version import POSTGRES_SCHEMA_VERSION


def _read_snapshot() -> ReadinessSnapshot:
    version, settings, queue, task = read_health_facts(read_connection, lambda: date.today())
    return ReadinessSnapshot(
        schema_version=version[0] if version is not None else None,
        settings_present=settings is not None,
        queue_state=queue["state"] if queue is not None else None,
        queue_refresh_pending=bool(queue["refresh_pending"]) if queue is not None else False,
        queue_last_error=queue["last_error"] if queue is not None else None,
        queue_task_state=task["state"] if task is not None else None,
        queue_task_payload_json=task["payload_json"] if task is not None else None,
    )


def _worker_queues() -> frozenset[str]:
    active = celery_app.control.inspect(timeout=1.0).active_queues() or {}
    return frozenset(queue["name"] for queues in active.values() for queue in queues)


def postgres_health() -> dict[str, object]:
    """Keep HTTP status translation at the root; construction performs no I/O."""
    try:
        return evaluate_readiness(read_snapshot=_read_snapshot, worker_queues=_worker_queues,
            today=date.today, expected_schema_version=POSTGRES_SCHEMA_VERSION)
    except ReadinessUnavailable as error:
        raise HTTPException(503, str(error)) from error
