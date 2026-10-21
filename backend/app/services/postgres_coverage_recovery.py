"""Recover one imported or interrupted Explorer run without a durable task."""

from __future__ import annotations

from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.controls import coverage as storage
from tempo_legacy_database.connections import connection
from .durable_tasks import enqueue_compact_postgres_task_in_transaction


def recover_one_explorer_run() -> bool:
    if not postgres_store.configured():
        return False
    with connection(background=True) as database:
        run = storage.recoverable_explorer_run(database)
        if run is None:
            return False
        enqueue_compact_postgres_task_in_transaction(
            database, "coverage_explorer", run["repertoire_id"],
            {"run_id": run["id"], "repertoire_id": run["repertoire_id"],
             "after_node_id": ""}, priority=80,
        )
        return True
