"""Foreground queue edits with PostgreSQL row locks and durable receipts."""

from __future__ import annotations

from .clock import date
import json
from typing import Any

from fastapi import HTTPException

from . import queue_application
from .queue_ordering import PRESENTATION_VERSION
from .gateway import register_command
from .review_conflicts import ReviewConflict
from tempo_learning_database.adapters.postgres_store import PostgresConnection
from tempo_learning_database.adapters.queue_position_lock import lock_queue_date_for_position
from .durable_tasks import enqueue_task_in_transaction
from .review_service import capture_daily_queue_order, preserve_daily_queue_order
from tempo_learning_database.adapters import queue_commands as storage
from .real_game_feedback import MISS_REASON


_lock_attempt_card = storage.lock_attempt_card


def available_attempt_sql() -> str:
    return queue_application.available_attempt_sql()


def validate_available_attempt(
    database, entry_id: int, card_id=None, expected_revision=None
) -> None:
    """A retained, identified attempt remains operable after queue promotion."""
    identified = card_id is not None and expected_revision is not None
    available = storage.available_attempt(database, date.today().isoformat(), entry_id, identified, MISS_REASON)
    if not available or available["id"] != entry_id:
        raise ReviewConflict(
            "queue_attempt_inactive", "This queue attempt is no longer active"
        )
    from .queue_attempt_origins import validate_failure_marker

    validate_failure_marker(database, entry_id, card_id, expected_revision)
    if identified:
        head = storage.queue_head(database, date.today().isoformat(), MISS_REASON)
        issued_origin = storage.issued_origin(database, entry_id, card_id, expected_revision)
        if (not head or head["id"] != entry_id) and not issued_origin:
            raise ReviewConflict(
                "queue_attempt_inactive",
                "This queue attempt was never issued as the active attempt",
            )


def issue_queue_head(database, payload: dict[str, Any]) -> dict[str, bool]:
    """Commit one head's issuance before returning it; never stamp prefetch."""
    entry_id = int(payload["entry_id"])
    if payload["queue_date"] != date.today().isoformat():
        return {"issued": False}
    _lock_attempt_card(database, entry_id)
    storage.lock_issue_queue_position(database, payload["queue_date"])
    head = storage.queue_head(database, payload["queue_date"], MISS_REASON)
    if not head or head["id"] != entry_id:
        return {"issued": False}
    try:
        validate_available_attempt(
            database, entry_id, payload["card_id"], payload["expected_revision"]
        )
    except ReviewConflict:
        return {"issued": False}
    prior = storage.issued_head_flag(database, entry_id, payload["card_id"], payload["expected_revision"])
    if prior and not prior[0]:
        from .remediation_adapter import bind_issued_attempt
        bind_issued_attempt(database, entry_id, payload["card_id"], payload["expected_revision"])
    changed = storage.mark_head_issued(database, entry_id, payload["card_id"], payload["expected_revision"])
    return {"issued": bool(changed)}


def mark_attempt_failed(
    database: PostgresConnection, payload: dict[str, Any]
) -> dict[str, bool]:
    entry_id = int(payload["entry_id"])
    _lock_attempt_card(database, entry_id)
    validate_available_attempt(
        database, entry_id, payload.get("card_id"), payload.get("expected_revision")
    )
    changed = storage.mark_attempt_failed(database, entry_id)
    if not changed:
        raise ReviewConflict(
            "queue_attempt_inactive", "This queue attempt is no longer active"
        )
    return {"attempt_failed": True}


def bury_queue_entry(
    database: PostgresConnection, payload: dict[str, Any]
) -> dict[str, Any]:
    entry_id = int(payload["entry_id"])
    queue_date = date.today().isoformat()
    _lock_attempt_card(database, entry_id)
    lock_queue_date_for_position(database, queue_date)
    validate_available_attempt(
        database, entry_id, payload.get("card_id"), payload.get("expected_revision")
    )
    # Keep today's rows as durable exclusion markers. Tomorrow's queue is
    # admitted normally, without changing the card's scheduling or reviews.
    previous_order = capture_daily_queue_order(database, queue_date)
    storage.bury_card_entries(database, queue_date, entry_id)
    preserve_daily_queue_order(database, queue_date, previous=previous_order)
    return {"buried": True, "queue_entry_id": entry_id}


def ensure_current_queue(
    database: PostgresConnection, payload: dict[str, Any]
) -> dict[str, Any]:
    """Request one durable queue refresh from the foreground Celery worker."""

    try:
        queue_date = date.fromisoformat(str(payload["queue_date"])).isoformat()
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(422, "A valid queue_date is required") from error
    active_task = storage.current_refresh_task(database)
    # An absent task cannot be locked: let refresh enqueue it before locking the
    # projection. A ready projection still needs no new task.
    projection = storage.refresh_projection(database, queue_date, bool(active_task))
    if (
        projection
        and projection["state"] == "ready"
        and not projection["refresh_pending"]
        and (not projection["has_queued_entries"]
             or (projection["presentation_membership_hash"] or "").startswith(PRESENTATION_VERSION + ":"))
    ):
        return {"queue_date": queue_date, "refresh_pending": False}
    if (
        active_task
        and active_task["state"] in {"queued", "leased", "retrying"}
        and json.loads(active_task["payload_json"]).get("queue_date") == queue_date
    ):
        storage.retain_refreshing_projection(database, queue_date, active_task["last_error"])
        return {
            "queue_date": queue_date,
            "refresh_pending": True,
            "task_id": active_task["id"],
        }
    task = request_queue_refresh_in_transaction(database, queue_date)
    return {"queue_date": queue_date, "refresh_pending": True, "task_id": task["id"]}


def request_queue_refresh_in_transaction(
    database: PostgresConnection,
    queue_date: str,
    *,
    preserve_through_entry_id: int | None = None,
) -> dict[str, Any]:
    return queue_application.request_queue_refresh_in_transaction(
        database,
        queue_date,
        preserve_through_entry_id=preserve_through_entry_id,
    )


def register_handlers():
    register_command("queue.issue_head", issue_queue_head)
    register_command("queue.attempt_failed", mark_attempt_failed)
    register_command("queue.bury", bury_queue_entry)
    register_command("queue.ensure_current", ensure_current_queue)
