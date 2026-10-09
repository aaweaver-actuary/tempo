"""Foreground queue edits with PostgreSQL row locks and durable receipts."""

from __future__ import annotations

from datetime import date
import json
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .review_conflicts import ReviewConflict
from .postgres_store import PostgresConnection
from .queue_position_lock import lock_queue_date_for_position
from .services.durable_tasks import enqueue_task_in_transaction
from .services.review_service import preserve_daily_queue_order


_QUEUE_REFRESH_PROJECTION_SQL = """INSERT INTO queue_projections(queue_date,state,generation,refresh_pending)
           VALUES(?,'refreshing',0,1) ON CONFLICT(queue_date) DO UPDATE SET
           state='refreshing',refresh_pending=1,last_error=NULL"""


def warm_queue_refresh_sql() -> None:
    """Prepare the queue-refresh statement outside a bounded transaction."""

    from .postgres_store import postgres_sql
    from .services.durable_tasks import warm_completion_sql

    warm_completion_sql()
    postgres_sql(_QUEUE_REFRESH_PROJECTION_SQL)


def _lock_attempt_card(database, entry_id: int) -> None:
    """Follow review's card-before-queue lock order for retained commands."""
    if not isinstance(database, PostgresConnection):
        return
    entry = database.execute("SELECT card_id FROM daily_queue WHERE id=?", (entry_id,)).fetchone()
    if entry:
        database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                                (f"tempo:card-edit:{entry['card_id']}",))
        database.execute("SELECT id FROM cards WHERE id=? FOR UPDATE", (entry['card_id'],))


def validate_available_attempt(database, entry_id: int, card_id=None, expected_revision=None) -> None:
    """A retained, identified attempt remains operable after queue promotion."""
    identified = card_id is not None and expected_revision is not None
    lock_clause = " FOR UPDATE OF q,c" if isinstance(database, PostgresConnection) else ""
    available = database.execute(
        f"""SELECT q.id FROM daily_queue q JOIN cards c ON c.id=q.card_id
            WHERE q.queue_date=? AND q.status='queued' AND c.archived=0
              AND c.pending_validation=0 AND c.superseded_by IS NULL
              AND NOT EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id=c.id)
              AND (c.content_type!='opening' OR EXISTS(
                  SELECT 1 FROM repertoires repertoire
                  WHERE (repertoire.id=c.repertoire_id OR EXISTS(
                      SELECT 1 FROM repertoire_cards link
                      WHERE link.card_id=c.id AND link.repertoire_id=repertoire.id))
                    AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks blocked
                        WHERE blocked.card_id=c.id AND blocked.repertoire_id=repertoire.id)))
              AND (c.content_type!='defense' OR
                   (SELECT include_defensive_cards_in_daily_stack FROM settings WHERE id=1)=1)
              {"AND q.id=?" if identified else ""}
            ORDER BY q.position,q.id LIMIT 1{lock_clause}""",
        (date.today().isoformat(), entry_id) if identified else (date.today().isoformat(),),
    ).fetchone()
    if not available or available["id"] != entry_id:
        raise ReviewConflict("queue_attempt_inactive", "This queue attempt is no longer active")
    from .queue_attempt_origins import validate_failure_marker
    validate_failure_marker(database, entry_id, card_id, expected_revision)


def mark_attempt_failed(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, bool]:
    entry_id = int(payload["entry_id"])
    _lock_attempt_card(database, entry_id)
    validate_available_attempt(database, entry_id, payload.get("card_id"), payload.get("expected_revision"))
    changed = database.execute(
        "UPDATE daily_queue SET attempt_failed=1 WHERE id=? AND status='queued'",
        (entry_id,),
    ).rowcount
    if not changed:
        raise ReviewConflict("queue_attempt_inactive", "This queue attempt is no longer active")
    return {"attempt_failed": True}


def bury_queue_entry(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    entry_id = int(payload["entry_id"])
    queue_date = date.today().isoformat()
    _lock_attempt_card(database, entry_id)
    lock_queue_date_for_position(database, queue_date)
    validate_available_attempt(database, entry_id, payload.get("card_id"), payload.get("expected_revision"))
    # Keep today's rows as durable exclusion markers. Tomorrow's queue is
    # admitted normally, without changing the card's scheduling or reviews.
    database.execute(
        """UPDATE daily_queue SET status='buried'
           WHERE queue_date=? AND status!='complete'
             AND card_id=(SELECT card_id FROM daily_queue WHERE id=?)""",
        (queue_date, entry_id),
    )
    preserve_daily_queue_order(database, queue_date)
    return {"buried": True, "queue_entry_id": entry_id}


def ensure_current_queue(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    """Request one durable queue refresh from the foreground Celery worker."""

    try:
        queue_date = date.fromisoformat(str(payload["queue_date"])).isoformat()
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(422, "A valid queue_date is required") from error
    # Every task/projection write locks the task first, including timeout recovery.
    active_task = database.execute(
        """SELECT id,state,payload_json,last_error FROM background_tasks
           WHERE kind='daily_queue' AND deduplication_key='current' FOR UPDATE""",
    ).fetchone()
    # An absent task cannot be locked: let refresh enqueue it before locking the
    # projection. A ready projection still needs no new task.
    projection_lock_clause = " FOR UPDATE" if active_task else ""
    projection = database.execute(
        "SELECT state,refresh_pending FROM queue_projections WHERE queue_date=?" + projection_lock_clause,
        (queue_date,),
    ).fetchone()
    if projection and projection["state"] == "ready" and not projection["refresh_pending"]:
        return {"queue_date": queue_date, "refresh_pending": False}
    if (active_task and active_task["state"] in {"queued", "leased", "retrying"}
            and json.loads(active_task["payload_json"]).get("queue_date") == queue_date):
        database.execute(
            """INSERT INTO queue_projections(queue_date,state,generation,refresh_pending,last_error)
               VALUES(?,'refreshing',0,1,?) ON CONFLICT(queue_date) DO UPDATE SET
               state='refreshing',refresh_pending=1,last_error=excluded.last_error""",
            (queue_date, active_task["last_error"]),
        )
        return {"queue_date": queue_date, "refresh_pending": True,
                "task_id": active_task["id"]}
    task = request_queue_refresh_in_transaction(database, queue_date)
    return {"queue_date": queue_date, "refresh_pending": True, "task_id": task["id"]}


def request_queue_refresh_in_transaction(database: PostgresConnection,
                                         queue_date: str, *, preserve_through_entry_id: int | None = None) -> dict[str, Any]:
    """Checkpoint a foreground mutation and its follow-up queue refresh together."""

    payload = {"queue_date": queue_date}
    if preserve_through_entry_id is not None:
        payload["preserve_through_entry_id"] = preserve_through_entry_id
    task = enqueue_task_in_transaction(
        database, "daily_queue", "current", payload, priority=10,
    )
    database.execute(
        _QUEUE_REFRESH_PROJECTION_SQL,
        (queue_date,),
    )
    return task


register_command("queue.attempt_failed", mark_attempt_failed)
register_command("queue.bury", bury_queue_entry)
register_command("queue.ensure_current", ensure_current_queue)
