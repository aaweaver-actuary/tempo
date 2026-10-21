"""Durable, restartable short sections; no optional producer dispatch."""

from datetime import datetime, timezone
import json, logging
import psycopg
from tempo_learning_remediation import RemediationError
from psycopg_pool import PoolTimeout
from tempo_learning_database.connections import connection
from .durable_tasks import claim
from .admission import admit, next_candidates
from . import frozen_opening_order, queue_ordering, queue_application

logger = logging.getLogger(__name__)


class SupersededWork(Exception):
    """Roll back a slice whose task generation/lease no longer authorizes it."""


def finish_slice(database, task, payload, more):
    if task["kind"] == "remediation":
        # Lock late: claims have committed and card/queue/module operations have
        # finished. Read the marker here, never from the claim-time snapshot.
        current = database.execute_native(
            """SELECT payload_json FROM background_tasks
               WHERE id=%s AND generation=%s AND lease_token=%s AND state='leased'
                 AND lease_expires_at>clock_timestamp() FOR UPDATE""",
            (task["id"], task["generation"], task["lease_token"]),
        ).fetchone()
        if current is None:
            raise SupersededWork()
        merged = json.loads(current[0])
        merged["after_card"] = payload.get("after_card", "")
        merged.setdefault("rescan_requested", False)
        if not more and merged["rescan_requested"]:
            merged.update(after_card="", rescan_requested=False)
            more = True
        payload = merged
    changed = database.execute_native(
        """UPDATE background_tasks SET payload_json=%s,state=%s,phase=%s,
               lease_token=NULL,lease_expires_at=NULL,last_error=NULL,updated_at=NOW()
           WHERE id=%s AND generation=%s AND lease_token=%s AND state='leased'
             AND lease_expires_at>clock_timestamp()
           RETURNING id""",
        (
            json.dumps(payload),
            "queued" if more else "complete",
            "queued" if more else "published",
            task["id"],
            task["generation"],
            task["lease_token"],
        ),
    ).fetchone()
    if not changed:
        raise SupersededWork()


def run_once():
    from .gateway import execute

    with connection(read_only=True, background=True) as database:
        operation = database.execute_native(
            "SELECT operation_id FROM operation_receipts WHERE state='queued' AND command_name!='publication.activate.v2' AND (next_retry_at IS NULL OR next_retry_at<=NOW()) ORDER BY created_at,operation_id LIMIT 1"
        ).fetchone()
    if operation:
        try:
            execute(operation[0], background=True)
        except Exception:
            # A retained intent must not starve queue maintenance while a card is busy.
            logger.exception("Essential command yielded; original identity retained")
            with connection(background=True) as database:
                database.execute_native(
                    "UPDATE operation_receipts SET next_retry_at=NOW()+INTERVAL '1 second' WHERE operation_id=%s AND state='queued'",
                    (operation[0],),
                )
        return True
    with connection(background=True) as database:
        task = claim(database)
    if task is None:
        return False
    try:
        return apply_task(task)
    except (psycopg.Error, PoolTimeout, RemediationError, SupersededWork):
        # Claiming committed separately. Release only this still-current lease
        # after the application transaction rolls back; preserve new generations.
        try:
            with connection(background=True) as database:
                database.execute_native(
                    """UPDATE background_tasks SET state='queued',lease_token=NULL,
                    lease_expires_at=NULL,last_error='Learning slice yielded',updated_at=NOW()
                    WHERE id=%s AND generation=%s AND lease_token=%s AND state='leased'""",
                    (task["id"], task["generation"], task["lease_token"]),
                )
        except (psycopg.Error, PoolTimeout):
            logger.info("Claim release yielded; lease expiry retains recovery")
        raise


def apply_task(task):
    # Release the task-row lock before acquiring card/queue/module locks. The
    # final fenced update commits its cursor with the actual application effects.
    with connection(background=True) as database:
        payload = json.loads(task["payload_json"])
        if task["kind"] == "remediation":
            from .remediation_adapter import maintain_one

            more = maintain_one(database, payload)
            finish_slice(database, task, payload, more)
            return True
        day = payload["queue_date"]
        cursor = payload.get("after_order_key", [])
        candidates = next_candidates(database, day, cursor)
        # One candidate commits alongside its cursor; refreshes coalesce into a new generation.
        if candidates:
            identifier = candidates[0]["id"]
            admit(database, identifier, day)
            payload["after_order_key"] = candidates[0]["sort_key"]
            finish_slice(database, task, payload, True)
        else:
            from tempo_learning_database.adapters.queue_position_lock import lock_queue_date_for_position

            token = frozen_opening_order.activation(database, lock=True)
            lock_queue_date_for_position(database, day)
            rows = queue_application.presentation_rows(database, day)
            membership_hash = frozen_opening_order.queue_hash(queue_ordering._queue_membership_hash(rows), token)
            saved = database.execute("SELECT seed,membership_hash FROM daily_queue_days WHERE queue_date=?", (day,)).fetchone()
            if not saved or saved["membership_hash"] != membership_hash:
                annotated = frozen_opening_order.annotate(database, rows) if token[0] is not None else rows
                seed, _, ordered = queue_ordering._plan_daily_queue_order(
                    annotated, day, saved,
                    preserve_through_entry_id=payload.get("preserve_through_entry_id"),
                )
                for slot, row in zip(sorted(rows, key=lambda row: (row['position'], row['id'])), ordered):
                    if slot['position'] != row['position']:
                        database.execute("UPDATE daily_queue SET position=? WHERE id=?", (slot['position'], row['id']))
                database.execute(
                    """INSERT INTO daily_queue_days(queue_date,seed,membership_hash,generated_at)
                       VALUES(?,?,?,?) ON CONFLICT(queue_date) DO UPDATE SET
                       membership_hash=excluded.membership_hash,generated_at=excluded.generated_at""",
                    (day, seed, membership_hash, datetime.now(timezone.utc).isoformat()),
                )
            # Match foreground task-before-projection order after card/queue
            # locks, preventing ensure_current's projection lock from inverting it.
            finish_slice(database, task, payload, False)
            database.execute_native(
                "INSERT INTO queue_projections(queue_date,state,generation,refresh_pending,updated_at) VALUES(%s,'ready',1,0,%s) ON CONFLICT(queue_date) DO UPDATE SET state='ready',generation=queue_projections.generation+1,refresh_pending=0,last_error=NULL,updated_at=excluded.updated_at",
                (day, datetime.now(timezone.utc).isoformat()),
            )
    return True


def safe_run_once():
    try:
        return run_once()
    except (psycopg.Error, PoolTimeout, SupersededWork):
        logger.info(
            "Essential maintenance yielded; durable work remains", exc_info=True
        )
        return False
    except RemediationError as error:
        if error.status != "retryable_failure":
            raise
        logger.info("Remediation yielded; durable work remains")
        return False
