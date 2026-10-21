"""Bounded cleanup of superseded, rebuildable priority generations."""

from __future__ import annotations

from tempo_legacy_database.connections import connection
from tempo_legacy_database.adapters.priority_lifecycle import retention
from tempo_legacy_database.adapters.priority_lifecycle.retention import postgres_store
from .activity_gate import activity_gate
from .durable_tasks import enqueue_task_in_transaction, lock_current_slice


ROWS_PER_SLICE = 16


def execute_priority_retention_slice(task: dict) -> bool:
    """Delete one short slice and atomically queue the next when work remains."""

    repertoire_id = task["payload"]["repertoire_id"]
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        if retention.native_configured():
            # Producers lock the priority job before enqueueing retention work.
            # Keep that order and protect its active generation through deletion.
            priority_job = retention.locked_current_job(database, repertoire_id)
            if not lock_current_slice(database, task):
                return False
        else:
            current_task = retention.current_lease(database, task["id"], task["generation"], task["lease_token"])
            if current_task is None:
                return False
        publication = retention.publication(database, repertoire_id)
        if publication is None and not retention.native_configured():
            return False
        if not retention.native_configured():
            priority_job = retention.current_job(database, repertoire_id)
        published_generation = int(publication["generation"]) if publication else -1
        active_generation = int(priority_job["generation"]) if priority_job else published_generation
        if retention.native_configured():
            keep_preparation = (
                active_generation if priority_job and priority_job["status"] in {"queued", "running"}
                else -1
            )
            # Preserve PostgreSQL's primary-key ordering through SQLite SQL translation.
            stale_preparation = retention.stale_preparation(database, repertoire_id, keep_preparation)
            prepared_keys = [] if stale_preparation is None else retention.prepared_keys(database, repertoire_id, stale_preparation["generation"], ROWS_PER_SLICE)
            retention.delete_prepared_rows(database, [(repertoire_id, generation, ordinal)
                 for generation, ordinal in prepared_keys])
            if stale_preparation is not None:
                retention.delete_empty_preparation(database, repertoire_id, stale_preparation["generation"])
            stale_keys = [] if prepared_keys else retention.stale_generation_keys(database, repertoire_id, published_generation, active_generation, ROWS_PER_SLICE)
            retention.delete_stale_generations(database, [(repertoire_id, generation, card_id) for generation, card_id in stale_keys])
        else:
            stale_row_ids = retention.stale_sqlite_row_ids(database, repertoire_id, published_generation, active_generation, ROWS_PER_SLICE)
            if stale_row_ids:
                retention.delete_sqlite_rows(database, [(row_id,) for row_id in stale_row_ids])
        more_stale_rows = retention.has_stale_generations(database, repertoire_id, published_generation, active_generation)
        if retention.native_configured():
            more_stale_rows = more_stale_rows or retention.has_stale_preparations(database, repertoire_id, keep_preparation)
        if more_stale_rows:
            enqueue_task_in_transaction(
                database,
                "priority_retention",
                repertoire_id,
                {"repertoire_id": repertoire_id},
                priority=200,
            )
        return more_stale_rows
