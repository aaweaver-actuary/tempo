"""Foreground queue edits with PostgreSQL row locks and durable receipts."""

from __future__ import annotations

from datetime import date
import json
import random
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .postgres_store import PostgresConnection
from .queue_position_lock import lock_queue_date_for_position
from .services.durable_tasks import enqueue_task_in_transaction


_ACTIVE_QUEUE_SQL = """SELECT q.id FROM daily_queue q JOIN cards c ON c.id=q.card_id
    WHERE q.queue_date=? AND q.status='queued'
      AND (c.content_type!='defense' OR
           (SELECT include_defensive_cards_in_daily_stack FROM settings WHERE id=1)=1)
    ORDER BY q.position,q.id"""


def mark_attempt_failed(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, bool]:
    entry_id = int(payload["entry_id"])
    active_entry = database.execute(
        f"{_ACTIVE_QUEUE_SQL} LIMIT 1 FOR UPDATE OF q", (date.today().isoformat(),),
    ).fetchone()
    if active_entry is None or active_entry["id"] != entry_id:
        raise HTTPException(409, "This queue attempt is no longer active")
    changed = database.execute(
        "UPDATE daily_queue SET attempt_failed=1 WHERE id=? AND status='queued'",
        (entry_id,),
    ).rowcount
    if not changed:
        raise HTTPException(409, "This queue attempt is no longer active")
    return {"attempt_failed": True}


def bury_queue_entry(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    entry_id = int(payload["entry_id"])
    queue_date = date.today().isoformat()
    lock_queue_date_for_position(database, queue_date)
    entry_ids = [row["id"] for row in database.execute(
        f"{_ACTIVE_QUEUE_SQL} FOR UPDATE OF q", (queue_date,),
    )]
    if not entry_ids or entry_ids[0] != entry_id:
        raise HTTPException(409, "This queue entry is no longer active")
    if len(entry_ids) < 2:
        raise HTTPException(409, "There are no other cards to move this card behind")
    entry_ids.remove(entry_id)
    entry_ids.insert(random.randint(1, len(entry_ids)), entry_id)
    database.execute(
        "UPDATE daily_queue SET position=position+1000000000 WHERE queue_date=? AND status='queued'",
        (queue_date,),
    )
    for position, queued_entry_id in enumerate(entry_ids):
        database.execute(
            "UPDATE daily_queue SET position=? WHERE id=? AND queue_date=? AND status='queued'",
            (position, queued_entry_id, queue_date),
        )
    return {"buried": True, "queue_entry_id": entry_id}


def ensure_current_queue(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    """Request one durable queue refresh from the foreground Celery worker."""

    try:
        queue_date = date.fromisoformat(str(payload["queue_date"])).isoformat()
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(422, "A valid queue_date is required") from error
    projection = database.execute(
        "SELECT state,refresh_pending FROM queue_projections WHERE queue_date=? FOR UPDATE",
        (queue_date,),
    ).fetchone()
    if projection and projection["state"] == "ready" and not projection["refresh_pending"]:
        return {"queue_date": queue_date, "refresh_pending": False}
    active_task = database.execute(
        """SELECT id,state,payload_json FROM background_tasks
           WHERE kind='daily_queue' AND deduplication_key='current' FOR UPDATE""",
    ).fetchone()
    if (active_task and active_task["state"] in {"queued", "leased", "retrying"}
            and json.loads(active_task["payload_json"]).get("queue_date") == queue_date):
        database.execute(
            """INSERT INTO queue_projections(queue_date,state,generation,refresh_pending)
               VALUES(?,'refreshing',0,1) ON CONFLICT(queue_date) DO UPDATE SET
               state='refreshing',refresh_pending=1,last_error=NULL""",
            (queue_date,),
        )
        return {"queue_date": queue_date, "refresh_pending": True,
                "task_id": active_task["id"]}
    task = request_queue_refresh_in_transaction(database, queue_date)
    return {"queue_date": queue_date, "refresh_pending": True, "task_id": task["id"]}


def request_queue_refresh_in_transaction(database: PostgresConnection,
                                         queue_date: str) -> dict[str, Any]:
    """Checkpoint a foreground mutation and its follow-up queue refresh together."""

    task = enqueue_task_in_transaction(
        database, "daily_queue", "current", {"queue_date": queue_date}, priority=10,
    )
    database.execute(
        """INSERT INTO queue_projections(queue_date,state,generation,refresh_pending)
           VALUES(?,'refreshing',0,1) ON CONFLICT(queue_date) DO UPDATE SET
           state='refreshing',refresh_pending=1,last_error=NULL""",
        (queue_date,),
    )
    return task


register_command("queue.attempt_failed", mark_attempt_failed)
register_command("queue.bury", bury_queue_entry)
register_command("queue.ensure_current", ensure_current_queue)
