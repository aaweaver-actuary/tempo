"""Durable, restartable short sections; no optional producer dispatch."""

from datetime import datetime, timezone
import json, logging
import psycopg
from psycopg_pool import PoolTimeout
from .database import connection
from .durable_tasks import claim
from .admission import admit, next_candidates
from . import frozen_opening_order, queue_ordering
from .review_service import preserve_daily_queue_order

logger = logging.getLogger(__name__)


def run_once():
    from .gateway import execute

    with connection(read_only=True, background=True) as database:
        operation = database.execute_native(
            "SELECT operation_id FROM operation_receipts WHERE state='queued' ORDER BY created_at,operation_id LIMIT 1"
        ).fetchone()
    if operation:
        execute(operation[0])
        return True
    with connection(background=True) as database:
        task = claim(database)
        if task is None:
            return False
        payload = json.loads(task["payload_json"])
        day = payload["queue_date"]
        cursor = payload.get("after_order_key", [])
        candidates = next_candidates(database, day, cursor)
        # One candidate commits alongside its cursor; refreshes coalesce into a new generation.
        if candidates:
            identifier = candidates[0]["id"]
            admit(database, identifier, day)
            payload["after_order_key"] = candidates[0]["sort_key"]
            database.execute_native(
                "UPDATE background_tasks SET payload_json=%s,state='queued',lease_token=NULL,lease_expires_at=NULL,updated_at=NOW() WHERE id=%s AND generation=%s AND lease_token=%s",
                (
                    json.dumps(payload),
                    task["id"],
                    task["generation"],
                    task["lease_token"],
                ),
            )
        else:
            from .queue_position_lock import lock_queue_date_for_position

            lock_queue_date_for_position(database, day)
            # Frozen readers only move unexposed introductions within their existing slots.
            rows = [
                dict(row)
                for row in database.execute(
                    "SELECT q.id,q.card_id,q.position,q.admission_kind,q.gameplay_priority_reason,c.content_type FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE q.queue_date=? AND q.status='queued' ORDER BY q.position,q.id",
                    (day,),
                ).fetchall()
            ]
            ordered = frozen_opening_order.order_queue_slots(
                database,
                rows,
                preserve_through_entry_id=payload.get("preserve_through_entry_id"),
            )
            for position, row in enumerate(ordered):
                database.execute(
                    "UPDATE daily_queue SET position=? WHERE id=?",
                    (position, row["id"]),
                )
            preserve_daily_queue_order(database, day)
            database.execute_native(
                "INSERT INTO queue_projections(queue_date,state,generation,refresh_pending,updated_at) VALUES(%s,'ready',1,0,%s) ON CONFLICT(queue_date) DO UPDATE SET state='ready',generation=queue_projections.generation+1,refresh_pending=0,last_error=NULL,updated_at=excluded.updated_at",
                (day, datetime.now(timezone.utc).isoformat()),
            )
            database.execute_native(
                "UPDATE background_tasks SET state='complete',phase='published',lease_token=NULL,lease_expires_at=NULL WHERE id=%s AND generation=%s AND lease_token=%s",
                (task["id"], task["generation"], task["lease_token"]),
            )
    return True


def safe_run_once():
    try:
        return run_once()
    except (psycopg.Error, PoolTimeout):
        logger.info(
            "Essential maintenance yielded; durable work remains", exc_info=True
        )
        return False
