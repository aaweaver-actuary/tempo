"""Foreground queue edits with PostgreSQL row locks and durable receipts."""

from __future__ import annotations

from datetime import date
import json
from typing import Any

from fastapi import HTTPException

from .services import queue_application
from .command_gateway import register_command
from .review_conflicts import ReviewConflict
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from tempo_legacy_database.adapters.queue_position_lock import lock_queue_date_for_position
from .services.durable_tasks import enqueue_task_in_transaction
from .services.review_service import capture_daily_queue_order, preserve_daily_queue_order


_QUEUE_REFRESH_PROJECTION_SQL = queue_application._QUEUE_REFRESH_PROJECTION_SQL


def warm_queue_refresh_sql() -> None:
    """Prepare the queue-refresh statement outside a bounded transaction."""

    from .services.durable_tasks import warm_completion_sql

    queue_storage.warm_queue_refresh_sql(warm_completion_sql)


from tempo_legacy_database.adapters.commands import queue as queue_storage
from tempo_legacy_database.adapters.commands.queue import _lock_attempt_card


_AVAILABLE_ATTEMPT_SQL = queue_application._AVAILABLE_ATTEMPT_SQL

def available_attempt_sql() -> str:
    return queue_application.available_attempt_sql()


def validate_available_attempt(database, entry_id: int, card_id=None, expected_revision=None) -> None:
    """A retained, identified attempt remains operable after queue promotion."""
    identified = card_id is not None and expected_revision is not None
    read_available = queue_storage.prepare_available_attempt(
        database, identified, entry_id, queue_application.MISS_REASON,
    )
    available = read_available(date.today().isoformat())
    if not available or available["id"] != entry_id:
        raise ReviewConflict("queue_attempt_inactive", "This queue attempt is no longer active")
    from .queue_attempt_origins import validate_failure_marker
    validate_failure_marker(database, entry_id, card_id, expected_revision)
    if identified:
        read_head = queue_storage.prepare_queue_head(database, queue_application.MISS_REASON)
        head = read_head(date.today().isoformat())
        issued_origin = queue_storage.issued_origin(database, entry_id, card_id, expected_revision)
        if (not head or head["id"] != entry_id) and not issued_origin:
            raise ReviewConflict("queue_attempt_inactive", "This queue attempt was never issued as the active attempt")


def issue_queue_head(database, payload: dict[str, Any]) -> dict[str, bool]:
    """Commit one head's issuance before returning it; never stamp prefetch."""
    entry_id = int(payload["entry_id"])
    if payload["queue_date"] != date.today().isoformat():
        return {"issued": False}
    _lock_attempt_card(database, entry_id)
    if queue_storage.is_postgres_connection(database):
        lock_queue_date_for_position(database, payload["queue_date"])
    read_head = queue_storage.prepare_queue_head(database, queue_application.MISS_REASON)
    head = read_head(payload["queue_date"])
    if not head or head["id"] != entry_id:
        return {"issued": False}
    try:
        validate_available_attempt(database, entry_id, payload["card_id"], payload["expected_revision"])
    except ReviewConflict:
        return {"issued": False}
    from .services.legacy_study_remediation import bind_first_issuance
    bind_first_issuance(database, entry_id, payload["card_id"], payload["expected_revision"])
    changed = queue_storage.stamp_issued_head(database, entry_id, payload["card_id"], payload["expected_revision"])
    return {"issued": bool(changed)}


def mark_attempt_failed(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, bool]:
    entry_id = int(payload["entry_id"])
    _lock_attempt_card(database, entry_id)
    validate_available_attempt(database, entry_id, payload.get("card_id"), payload.get("expected_revision"))
    changed = queue_storage.mark_attempt_failed(database, entry_id)
    if not changed:
        raise ReviewConflict("queue_attempt_inactive", "This queue attempt is no longer active")
    return {"attempt_failed": True}


def bury_queue_entry(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    entry_id = int(payload["entry_id"])
    queue_date = date.today().isoformat()
    _lock_attempt_card(database, entry_id)
    lock_queue_date_for_position(database, queue_date)
    validate_available_attempt(database, entry_id, payload.get("card_id"), payload.get("expected_revision"))
    previous_order = capture_daily_queue_order(database, queue_date)
    # Keep today's rows as durable exclusion markers. Tomorrow's queue is
    # admitted normally, without changing the card's scheduling or reviews.
    queue_storage.bury_attempt_card(database, queue_date, entry_id)
    preserve_daily_queue_order(database, queue_date, previous=previous_order)
    return {"buried": True, "queue_entry_id": entry_id}


def ensure_current_queue(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    """Request one durable queue refresh from the foreground Celery worker."""

    try:
        queue_date = date.fromisoformat(str(payload["queue_date"])).isoformat()
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(422, "A valid queue_date is required") from error
    # Every task/projection write locks the task first, including timeout recovery.
    active_task = queue_storage.lock_current_queue_task(database)
    # An absent task cannot be locked: let refresh enqueue it before locking the
    # projection. A ready projection still needs no new task.
    projection = queue_storage.current_projection(database, queue_date, active_task)
    if projection and projection["state"] == "ready" and not projection["refresh_pending"]:
        return {"queue_date": queue_date, "refresh_pending": False}
    if (active_task and active_task["state"] in {"queued", "leased", "retrying"}
            and json.loads(active_task["payload_json"]).get("queue_date") == queue_date):
        queue_storage.mark_active_refresh(database, queue_date, active_task["last_error"])
        return {"queue_date": queue_date, "refresh_pending": True,
                "task_id": active_task["id"]}
    task = request_queue_refresh_in_transaction(database, queue_date)
    return {"queue_date": queue_date, "refresh_pending": True, "task_id": task["id"]}


def request_queue_refresh_in_transaction(database: PostgresConnection,
                                         queue_date: str, *, preserve_through_entry_id: int | None = None) -> dict[str, Any]:
    return queue_application.request_queue_refresh_in_transaction(
        database, queue_date, preserve_through_entry_id=preserve_through_entry_id,
    )


register_command("queue.issue_head", issue_queue_head)
register_command("queue.attempt_failed", mark_attempt_failed)
register_command("queue.bury", bury_queue_entry)
register_command("queue.ensure_current", ensure_current_queue)
