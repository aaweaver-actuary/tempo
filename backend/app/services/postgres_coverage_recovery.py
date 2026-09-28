"""Recover one imported or interrupted Explorer run without a durable task."""

from __future__ import annotations

from .. import postgres_store
from ..database import connection
from .durable_tasks import enqueue_compact_postgres_task_in_transaction


def recover_one_explorer_run() -> bool:
    if not postgres_store.configured():
        return False
    with connection(background=True) as database:
        run = database.execute_native(
            "SELECT r.id,r.repertoire_id FROM repertoire_coverage_runs r "
            "LEFT JOIN background_activity control ON control.source='coverage' "
            "AND control.work_id=r.id "
            "WHERE r.status IN ('queued','running') "
            "AND COALESCE(control.paused,0)=0 "
            "AND EXISTS (SELECT 1 FROM repertoire_coverage_nodes n "
            "WHERE n.run_id=r.id AND n.explorer_status='queued') "
            "AND NOT EXISTS (SELECT 1 FROM background_tasks task "
            "WHERE task.kind='coverage_explorer' "
            "AND task.deduplication_key=r.repertoire_id "
            "AND task.state IN ('queued','leased','retrying') "
            "AND task.payload_json::jsonb->>'run_id'=r.id) "
            "ORDER BY r.created_at,r.id LIMIT 1 FOR UPDATE OF r SKIP LOCKED",
        ).fetchone()
        if run is None:
            return False
        enqueue_compact_postgres_task_in_transaction(
            database, "coverage_explorer", run["repertoire_id"],
            {"run_id": run["id"], "repertoire_id": run["repertoire_id"],
             "after_node_id": ""}, priority=80,
        )
        return True
