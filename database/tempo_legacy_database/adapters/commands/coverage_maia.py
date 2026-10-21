"""Existing coverage Maia lease queries; caller owns policy and transaction."""
from ..repertoire_scope.freshness import coverage_scope_predicate, latest_coverage_attempt_predicate


def node_owner(database, node_id):
    return database.execute_native(
        "SELECT repertoire_id FROM repertoire_coverage_nodes WHERE id=%s",
        (node_id,),
    ).fetchone()


def locked_lease(database, node_id):
    return database.execute_native(
        "SELECT n.*,r.settings_json,r.status AS run_status FROM repertoire_coverage_nodes n "
        "JOIN repertoire_coverage_runs r ON r.id=n.run_id "
        f"WHERE n.id=%s AND {latest_coverage_attempt_predicate(database, native=True)} FOR UPDATE OF n,r",
        (node_id,),
    ).fetchone()


def claim_candidate(database, now):
    return database.execute_native(
        "SELECT n.id,n.run_id,n.repertoire_id,n.fen,r.settings_json FROM repertoire_coverage_nodes n "
        "JOIN repertoire_coverage_runs r ON r.id=n.run_id "
        "LEFT JOIN background_activity control ON control.source='coverage' "
        "AND control.work_id=n.run_id "
        "WHERE r.status IN ('queued','running','complete','failed') "
        "AND (n.maia_status='queued' OR (n.maia_status='leased' AND n.lease_expires_at<%s)) "
        "AND COALESCE(control.paused,0)=0 "
        f"AND {coverage_scope_predicate(database, native=True)} "
        f"AND {latest_coverage_attempt_predicate(database, native=True)} "
        "ORDER BY COALESCE(control.promoted,0) DESC,r.created_at,n.ply,n.id "
        "LIMIT 1",
        (now,),
    ).fetchone()


def locked_candidate(database, node_id):
    return database.execute_native(
        "SELECT maia_status,lease_expires_at FROM repertoire_coverage_nodes WHERE id=%s FOR UPDATE SKIP LOCKED",
        (node_id,),
    ).fetchone()


def mark_leased(database, lease_id, expires_at, now, node_id):
    database.execute_native(
        "UPDATE repertoire_coverage_nodes SET maia_status='leased',lease_id=%s,"
        "lease_expires_at=%s,updated_at=%s WHERE id=%s",
        (lease_id, expires_at, now, node_id),
    )


def heartbeat(database, expires_at, now, node_id):
    database.execute_native(
        "UPDATE repertoire_coverage_nodes SET lease_expires_at=%s,updated_at=%s WHERE id=%s",
        (expires_at, now, node_id),
    )


def release(database, now, node_id, lease_id):
    return database.execute_native(
        "UPDATE repertoire_coverage_nodes SET maia_status='queued',lease_id=NULL,"
        "lease_expires_at=NULL,updated_at=%s "
        "WHERE id=%s AND maia_status='leased' AND lease_id=%s RETURNING id",
        (now, node_id, lease_id),
    ).fetchone()


def fail_node(database, message, now, node_id):
    database.execute_native(
        "UPDATE repertoire_coverage_nodes SET maia_status='failed',lease_id=NULL,"
        "lease_expires_at=NULL,last_error=%s,updated_at=%s WHERE id=%s",
        (message, now, node_id),
    )


def fail_run(database, message, now, run_id):
    database.execute_native(
        "UPDATE repertoire_coverage_runs SET status='failed',last_error=%s,updated_at=%s "
        "WHERE id=%s",
        (message, now, run_id),
    )


def upsert_candidates(database, node_id, covered_replies, moves_json):
    database.execute_native(
        "INSERT INTO repertoire_coverage_candidates("
        "node_id,move_uci,maia_probability,covered,source_state) "
        "SELECT %s,move_uci,probability,"
        "CASE WHEN move_uci=ANY(%s::text[]) THEN 1 ELSE 0 END,'maia-only' "
        "FROM jsonb_to_recordset(%s::jsonb) "
        "AS move(move_uci text,probability double precision) "
        "ON CONFLICT(node_id,move_uci) DO UPDATE SET "
        "maia_probability=excluded.maia_probability,covered=excluded.covered",
        (node_id, covered_replies, moves_json),
    )


def mark_complete(database, now, node_id):
    database.execute_native(
        "UPDATE repertoire_coverage_nodes SET maia_status='complete',lease_id=NULL,"
        "lease_expires_at=NULL,updated_at=%s WHERE id=%s",
        (now, node_id),
    )


def record_progress(database, run_id, generation, now, source_run_id):
    database.execute_native(
        "INSERT INTO background_activity(source,work_id,generation_key,phase,"
        "completed_units,total_units,updated_at) "
        "SELECT 'coverage',%s,%s,'Checking positions',"
        "COUNT(*) FILTER (WHERE explorer_status='complete') + "
        "COUNT(*) FILTER (WHERE maia_status='complete'),COUNT(*)*2,%s "
        "FROM repertoire_coverage_nodes WHERE run_id=%s "
        "ON CONFLICT(source,work_id) DO UPDATE SET "
        "generation_key=excluded.generation_key,phase=excluded.phase,"
        "completed_units=excluded.completed_units,total_units=excluded.total_units,"
        "updated_at=excluded.updated_at",
        (run_id, generation, now, source_run_id),
    )
