"""Durable, restartable short sections; no optional producer dispatch."""

from datetime import datetime, timezone
import json, logging
from tempo_learning_database.adapters import maintenance as storage
from tempo_learning_database.adapters.maintenance import psycopg, PoolTimeout
from tempo_learning_remediation import RemediationError
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
        current = storage.leased_task_payload_for_update(database, task)
        if current is None:
            raise SupersededWork()
        merged = json.loads(current[0])
        merged["after_card"] = payload.get("after_card", "")
        merged.setdefault("rescan_requested", False)
        if not more and merged["rescan_requested"]:
            merged.update(after_card="", rescan_requested=False)
            more = True
        payload = merged
    changed = storage.finish_task_slice(
        database, task, payload, "queued" if more else "complete", "queued" if more else "published",
    )
    if not changed:
        raise SupersededWork()


def run_once():
    from .gateway import execute

    with connection(read_only=True, background=True) as database:
        operation = storage.next_queued_operation(database)
    if operation:
        try:
            execute(operation[0], background=True)
        except Exception:
            # A retained intent must not starve queue maintenance while a card is busy.
            logger.exception("Essential command yielded; original identity retained")
            with connection(background=True) as database:
                storage.defer_queued_operation(database, operation[0])
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
                storage.release_yielded_task(database, task)
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
            saved = storage.queue_day_order(database, day)
            if not saved or saved["membership_hash"] != membership_hash:
                annotated = frozen_opening_order.annotate(database, rows) if token[0] is not None else rows
                seed, _, ordered = queue_ordering._plan_daily_queue_order(
                    annotated, day, saved,
                    preserve_through_entry_id=payload.get("preserve_through_entry_id"),
                )
                for slot, row in zip(sorted(rows, key=lambda row: (row['position'], row['id'])), ordered):
                    if slot['position'] != row['position']:
                        storage.set_queue_position(database, slot['position'], row['id'])
                storage.save_queue_day_order(database, day, seed, membership_hash, datetime.now(timezone.utc).isoformat())
            # Match foreground task-before-projection order after card/queue
            # locks, preventing ensure_current's projection lock from inverting it.
            finish_slice(database, task, payload, False)
            storage.mark_queue_projection_ready(database, day, datetime.now(timezone.utc).isoformat())
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
