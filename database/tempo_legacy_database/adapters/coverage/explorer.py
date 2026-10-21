"""Existing fixed coverage Explorer publication queries; caller owns policy and transaction."""

from ..repertoire_scope.freshness import latest_coverage_attempt_predicate


def upsert_cache(database, values):
    database.execute_native(
        "INSERT INTO explorer_position_cache(cache_key,fen_key,speeds,ratings,response_json,fetched_at) "
        "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(cache_key) DO UPDATE SET "
        "response_json=excluded.response_json,fetched_at=excluded.fetched_at",
        values,
    )


def complete_node(database, values):
    database.execute_native(
        "UPDATE repertoire_coverage_nodes SET explorer_status='complete',explorer_games=%s,"
        "last_error=NULL,updated_at=%s WHERE id=%s",
        values,
    )


def publish_progress(database, values):
    database.execute_native(
        "INSERT INTO background_activity(source,work_id,generation_key,phase,"
        "completed_units,total_units,updated_at) "
        "VALUES('coverage',%s,%s,'Checking positions',%s,%s,%s) "
        "ON CONFLICT(source,work_id) DO UPDATE SET "
        "generation_key=excluded.generation_key,phase=excluded.phase,"
        "completed_units=excluded.completed_units,total_units=excluded.total_units,"
        "updated_at=excluded.updated_at",
        values,
    )


def fail_node(database, values):
    database.execute_native(
        "UPDATE repertoire_coverage_nodes SET explorer_status='failed',last_error=%s,updated_at=%s "
        "WHERE id=%s AND explorer_status='queued'",
        values,
    )


def fail_active_run(database, values):
    database.execute_native(
        "UPDATE repertoire_coverage_runs SET status='failed',last_error=%s,updated_at=%s "
        "WHERE id=%s AND status IN ('queued','running')",
        values,
    )


def upsert_candidates(database, values):
    database.execute_native(
        "INSERT INTO repertoire_coverage_candidates("
        "node_id,move_uci,explorer_probability,covered,source_state) "
        "SELECT %s,move_uci,explorer_probability,covered,'explorer-only' "
        "FROM jsonb_to_recordset(%s::jsonb) AS candidate("
        "move_uci text,explorer_probability double precision,covered bigint) "
        "ON CONFLICT(node_id,move_uci) DO UPDATE SET "
        "explorer_probability=excluded.explorer_probability,covered=excluded.covered",
        values,
    )


def locked_node(database, values):
    return database.execute_native(
        "SELECT n.*,r.settings_json,r.status AS run_status,r.total_nodes "
        "FROM repertoire_coverage_nodes n JOIN repertoire_coverage_runs r ON r.id=n.run_id "
        f"WHERE n.id=%s AND {latest_coverage_attempt_predicate(database, native=True)} FOR UPDATE OF n,r", values,
    ).fetchone()


def advance_run(database, values):
    return database.execute_native(
        "UPDATE repertoire_coverage_runs SET completed_nodes=completed_nodes+1,"
        "status=CASE WHEN completed_nodes+1>=total_nodes THEN 'complete' ELSE 'running' END,"
        "updated_at=%s WHERE id=%s RETURNING completed_nodes,status,total_nodes",
        values,
    ).fetchone()


def task_lease(database, values):
    return database.execute_native(
        "SELECT generation,lease_token,state FROM background_tasks WHERE id=%s",
        values,
    ).fetchone()


def next_queued_node(database, values):
    return database.execute_native(
        "SELECT n.id,n.run_id,n.repertoire_id,n.fen,n.fen_key,n.covered_replies_json,"
        "r.settings_json FROM repertoire_coverage_nodes n "
        "JOIN repertoire_coverage_runs r ON r.id=n.run_id "
        "LEFT JOIN background_activity control ON control.source='coverage' "
        "AND control.work_id=n.run_id "
        "WHERE n.run_id=%s AND n.id>%s AND n.explorer_status='queued' "
        "AND r.status IN ('queued','running','failed') AND COALESCE(control.paused,0)=0 "
        f"AND {latest_coverage_attempt_predicate(database, native=True)} ORDER BY n.id LIMIT 1",
        values,
    ).fetchone()


def maia_completed_count(database, values):
    return database.execute_native(
        "SELECT COUNT(*) FROM repertoire_coverage_nodes WHERE run_id=%s AND maia_status='complete'",
        values,
    ).fetchone()[0]
