"""Narrow durable Learning queue work, with database-selected identity."""

import json
from tempo_learning_database.adapters import durable_tasks as storage


def enqueue_task_in_transaction(
    database,
    kind,
    deduplication_key,
    payload,
    *,
    priority=10,
    max_attempts=5,
    delay_seconds=0,
    minimum_generation=0,
):
    if kind not in {"daily_queue", "remediation"}:
        raise ValueError("Only essential daily_queue and remediation work is registered")
    if kind == "remediation":
        return enqueue_remediation_in_transaction(
            database, deduplication_key, payload, priority=priority,
            max_attempts=max_attempts, delay_seconds=delay_seconds,
            minimum_generation=minimum_generation,
        )
    return storage.enqueue_daily_task_in_transaction(
        database, kind, deduplication_key, payload, priority=priority,
        max_attempts=max_attempts, delay_seconds=delay_seconds,
        minimum_generation=minimum_generation,
    )


def enqueue_remediation_in_transaction(
    database, deduplication_key, payload, *, priority, max_attempts,
    delay_seconds, minimum_generation,
):
    """Coalesce refreshes without changing a productive sweep's ownership/order.

    Call only after encompassing application locks. INSERT also serializes the
    absent-row race; the row lock is retained in the caller's transaction.
    """
    initial = {**payload, "after_card": "", "rescan_requested": False}
    task, created = storage.create_or_lock_remediation_task(
        database, deduplication_key, initial, priority=priority, max_attempts=max_attempts,
        delay_seconds=delay_seconds, minimum_generation=minimum_generation,
    )
    event = "enqueued"
    if not created:
        current = json.loads(task["payload_json"])
        if current["queue_date"] > initial["queue_date"]:
            return dict(task)
        if (current["queue_date"] == initial["queue_date"]
                and task["state"] in {"queued", "leased", "retrying"}):
            current["rescan_requested"] = True
            task = storage.set_remediation_payload(database, task, current)
            event = "rescan_requested"
        else:
            task = storage.reset_remediation_task(
                database, task, initial, priority=priority, max_attempts=max_attempts,
                delay_seconds=delay_seconds, minimum_generation=minimum_generation,
            )
    storage.record_remediation_event(database, task, event)
    return dict(task)


claim = storage.claim
