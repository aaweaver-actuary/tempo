"""Coverage projections and bounded recovery reads on the existing caller transaction."""
import json


def candidate_probability_rows(database, node_id: str):
    return database.execute_native(
        "SELECT move_uci,explorer_probability,maia_probability "
        "FROM repertoire_coverage_candidates WHERE node_id=%s", (node_id,),
    ).fetchall()


def save_candidate_projection(database, node_id: str, updates: list[dict]) -> None:
    database.execute_native(
        "UPDATE repertoire_coverage_candidates candidate SET "
        "blended_probability=updated.blended,required=updated.required,"
        "source_state=updated.source_state "
        "FROM jsonb_to_recordset(%s::jsonb) AS updated("
        "move_uci text,blended double precision,required bigint,source_state text) "
        "WHERE candidate.node_id=%s AND candidate.move_uci=updated.move_uci",
        (json.dumps(updates), node_id),
    )


def recoverable_explorer_run(database):
    return database.execute_native(
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
