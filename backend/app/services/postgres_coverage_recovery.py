"""Recover one imported or interrupted Explorer run without a durable task."""

from __future__ import annotations

from datetime import datetime, timezone

from .. import postgres_store
from ..database import connection
from .canonical_scope_freshness import latest_coverage_attempt_predicate
from .repertoire_coverage import get_explorer_session_token
from .durable_tasks import enqueue_compact_postgres_task_in_transaction


def recover_one_explorer_run() -> bool:
    if not postgres_store.configured():
        return False
    # Redis is outside the short SQL transaction. An unavailable store remains
    # unknown and cannot reset source state or cause automatic rejection loops.
    registered = bool(get_explorer_session_token())
    with connection(background=True) as database:
        run = database.execute_native(
            "SELECT r.id,r.repertoire_id FROM repertoire_coverage_runs r "
            "LEFT JOIN background_activity control ON control.source='coverage' "
            "AND control.work_id=r.id "
            "WHERE r.status IN ('queued','running','failed') "
            f"AND {latest_coverage_attempt_predicate(database, native=True)} "
            "AND COALESCE(control.paused,0)=0 "
            "AND EXISTS (SELECT 1 FROM repertoire_coverage_nodes n "
            "WHERE n.run_id=r.id AND ((n.explorer_status='queued' AND (n.explorer_retry_at IS NULL OR n.explorer_retry_at<=%s)) "
            "OR (%s AND n.explorer_status='failed' AND n.explorer_failure_code IN ('registration_missing','credential_rejected')))) "
            "AND NOT EXISTS (SELECT 1 FROM background_tasks task "
            "WHERE task.kind='coverage_explorer' "
            "AND task.deduplication_key=r.repertoire_id "
            "AND task.state IN ('queued','leased','retrying') "
            "AND task.payload_json::jsonb->>'run_id'=r.id) "
            "ORDER BY r.created_at,r.id LIMIT 1 FOR UPDATE OF r SKIP LOCKED",
            (datetime.now(timezone.utc).isoformat(), registered),
        ).fetchone()
        if run is None:
            return False
        if registered:
            database.execute_native(
                "UPDATE repertoire_coverage_nodes SET explorer_status='queued',explorer_failure_code=NULL,"
                "explorer_retry_at=NULL,explorer_error=NULL,last_error=NULL WHERE id=("
                "SELECT id FROM repertoire_coverage_nodes WHERE run_id=%s AND explorer_status='failed' "
                "AND explorer_failure_code IN ('registration_missing','credential_rejected') ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED)",
                (run['id'],))
        database.execute_native("UPDATE repertoire_coverage_runs SET status='running' WHERE id=%s", (run['id'],))
        enqueue_compact_postgres_task_in_transaction(
            database, "coverage_explorer", run["repertoire_id"],
            {"run_id": run["id"], "repertoire_id": run["repertoire_id"],
             "after_node_id": ""}, priority=80,
        )
        return True
