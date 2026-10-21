"""Existing analysis lease/report queries on the caller-owned transaction.

Claim eligibility, retries, UUID/time selection, engine validation and HTTP
outcomes remain in the command module. No cursor leaves these operations.
"""


def next_parent(database, now):
    return database.execute_native(
        "SELECT j.game_id,j.analysis_version,j.analysis_evidence_version,"
        "g.provider,g.username,g.played_at,g.color,g.start_fen,g.moves_json,"
        "c.divergence_ply "
        "FROM game_analysis_jobs j JOIN imported_games g ON g.id=j.game_id "
        "LEFT JOIN current_repertoire_comparisons c ON c.game_id=g.id "
        "LEFT JOIN background_activity control ON control.source='game_analysis' "
        "AND control.work_id=j.game_id "
        "WHERE (j.status='queued' OR (j.status='leased' AND j.lease_expires_at<%s)) "
        "AND g.rated=1 AND g.speed IN ('blitz','rapid','classical') "
        "AND COALESCE(control.paused,0)=0 "
        "ORDER BY COALESCE(control.promoted,0) DESC,g.played_at DESC,j.game_id "
        "LIMIT 1 FOR UPDATE OF j,g SKIP LOCKED",
        (now,),
    ).fetchone()


def lease_parent(database, game_id, lease_id, expires_at, now):
    return database.execute_native(
        "UPDATE game_analysis_jobs SET status='leased',lease_id=%s,lease_expires_at=%s,"
        "attempts=attempts+1,updated_at=%s WHERE game_id=%s",
        (lease_id, expires_at, now, game_id),
    ).rowcount


def mark_game_analyzing(database, game_id):
    database.execute_native(
        "UPDATE imported_games SET analysis_state='analyzing' WHERE id=%s", (game_id,),
    )


def mark_game_pending(database, game_id):
    database.execute_native(
        "UPDATE imported_games SET analysis_state='pending' WHERE id=%s", (game_id,),
    )


def mark_game_failed(database, game_id):
    database.execute_native(
        "UPDATE imported_games SET analysis_state='failed' WHERE id=%s", (game_id,),
    )


def set_game_analysis_state(database, game_id, state):
    database.execute_native(
        "UPDATE imported_games SET analysis_state=%s WHERE id=%s", (state, game_id),
    )


def prepared_candidate(database, preferred_game_id, now):
    return database.execute_native(
        "SELECT j.game_id,j.analysis_version,j.analysis_evidence_version,"
        "g.start_fen,g.moves_json,c.divergence_ply "
        "FROM game_analysis_jobs j JOIN imported_games g ON g.id=j.game_id "
        "LEFT JOIN current_repertoire_comparisons c ON c.game_id=g.id "
        "LEFT JOIN background_activity control ON control.source='game_analysis' "
        "AND control.work_id=j.game_id "
        "WHERE (j.status='queued' OR (j.status='leased' AND j.lease_expires_at<%s)) "
        "AND g.rated=1 AND g.speed IN ('blitz','rapid','classical') "
        "AND COALESCE(control.paused,0)=0 "
        + ("AND j.game_id=%s " if preferred_game_id is not None else "") +
        "ORDER BY COALESCE(control.promoted,0) DESC,g.played_at DESC,j.game_id LIMIT 1",
        (now, *((preferred_game_id,) if preferred_game_id is not None else ())),
    ).fetchone()


def saved_position_reports(database, game_id, analysis_version):
    return [dict(row) for row in database.execute_native(
        "SELECT scan_pass,position_index,report_json,state FROM game_analysis_position_reports "
        "WHERE game_id=%s AND analysis_version=%s",
        (game_id, analysis_version),
    )]


def lock_parent(database, game_id):
    return database.execute_native(
        "SELECT j.analysis_version,j.analysis_evidence_version,j.status,j.lease_expires_at,"
        "g.rated,g.speed FROM game_analysis_jobs j JOIN imported_games g ON g.id=j.game_id "
        "WHERE j.game_id=%s FOR UPDATE OF j,g SKIP LOCKED",
        (game_id,),
    ).fetchone()


def analysis_control(database, game_id):
    return database.execute_native(
        "SELECT paused FROM background_activity WHERE source='game_analysis' AND work_id=%s",
        (game_id,),
    ).fetchone()


def lock_position(database, game_id, version, scan_pass, position_index):
    return database.execute_native(
        "SELECT id,state,lease_expires_at FROM game_analysis_position_reports "
        "WHERE game_id=%s AND analysis_version=%s AND scan_pass=%s AND position_index=%s FOR UPDATE",
        (game_id, version, scan_pass, position_index),
    ).fetchone()


def fail_exhausted_parent(database, game_id, now):
    database.execute_native(
        "UPDATE game_analysis_jobs SET status='failed',lease_id=NULL,lease_expires_at=NULL,"
        "last_error='A game position exhausted its engine retries',updated_at=%s "
        "WHERE game_id=%s", (now, game_id),
    )


def add_terminal_position(database, report_id, game_id, version, scan_pass, position_index, report_json, now):
    return database.execute_native(
        "INSERT INTO game_analysis_position_reports("
        "id,game_id,analysis_version,scan_pass,position_index,request_json,report_json,state,updated_at) "
        "VALUES(%s,%s,%s,%s,%s,'{}',%s,'complete',%s) "
        "ON CONFLICT(game_id,analysis_version,scan_pass,position_index) DO NOTHING",
        (report_id, game_id, version, scan_pass, position_index, report_json, now),
    ).rowcount


def requeue_terminal_parent(database, game_id, now):
    database.execute_native(
        "UPDATE game_analysis_jobs SET status='queued',lease_id=NULL,lease_expires_at=NULL,"
        "updated_at=%s WHERE game_id=%s", (now, game_id),
    )


def lease_existing_position(database, report_id, lease_id, parent_lease_id, expires_at, request_json, now):
    return database.execute_native(
        "UPDATE game_analysis_position_reports SET state='leased',lease_id=%s,"
        "parent_lease_id=%s,lease_expires_at=%s,request_json=%s,attempts=attempts+1,"
        "updated_at=%s WHERE id=%s",
        (lease_id, parent_lease_id, expires_at, request_json, now, report_id),
    ).rowcount


def lease_new_position(database, report_id, game_id, version, scan_pass, position_index,
                       request_json, lease_id, parent_lease_id, expires_at, now):
    return database.execute_native(
        "INSERT INTO game_analysis_position_reports("
        "id,game_id,analysis_version,scan_pass,position_index,request_json,state,"
        "lease_id,parent_lease_id,lease_expires_at,attempts,updated_at) "
        "VALUES(%s,%s,%s,%s,%s,%s,'leased',%s,%s,%s,1,%s)",
        (report_id, game_id, version, scan_pass, position_index, request_json,
         lease_id, parent_lease_id, expires_at, now),
    ).rowcount


def report_request(database, report_id):
    return database.execute_native(
        "SELECT request_json FROM game_analysis_position_reports WHERE id=%s", (report_id,),
    ).fetchone()


def lock_report_for_publication(database, report_id):
    return database.execute_native(
        "SELECT game_id,request_json,report_json,state,lease_id,parent_lease_id "
        "FROM game_analysis_position_reports WHERE id=%s FOR UPDATE", (report_id,),
    ).fetchone()


def complete_position(database, report_id, report_json, now):
    database.execute_native(
        "UPDATE game_analysis_position_reports SET state='complete',report_json=%s,"
        "lease_id=NULL,parent_lease_id=NULL,lease_expires_at=NULL,last_error=NULL,updated_at=%s "
        "WHERE id=%s", (report_json, now, report_id),
    )


def release_completed_position_parent(database, game_id, parent_lease_id, now):
    return database.execute_native(
        "UPDATE game_analysis_jobs SET status='queued',lease_id=NULL,lease_expires_at=NULL,"
        "last_error=NULL,updated_at=%s WHERE game_id=%s AND status='leased' AND lease_id=%s "
        "RETURNING game_id", (now, game_id, parent_lease_id),
    ).fetchone()


def lock_position_release(database, report_id, lease_id):
    return database.execute_native(
        "SELECT game_id,parent_lease_id,attempts FROM game_analysis_position_reports "
        "WHERE id=%s AND state='leased' AND lease_id=%s FOR UPDATE",
        (report_id, lease_id),
    ).fetchone()


def release_position(database, report_id, state, error, now):
    database.execute_native(
        "UPDATE game_analysis_position_reports SET state=%s,lease_id=NULL,parent_lease_id=NULL,"
        "lease_expires_at=NULL,last_error=%s,updated_at=%s WHERE id=%s",
        (state, error, now, report_id),
    )


def record_position_error(database, report_id, game_id, error, now):
    database.execute_native(
        "INSERT INTO game_analysis_position_errors(report_id,game_id,error,recorded_at) "
        "VALUES(%s,%s,%s,%s)", (report_id, game_id, error, now),
    )


def release_failed_position_parent(database, game_id, parent_lease_id, state, error, now):
    return database.execute_native(
        "UPDATE game_analysis_jobs SET status=%s,lease_id=NULL,lease_expires_at=NULL,"
        "last_error=%s,updated_at=%s WHERE game_id=%s AND status='leased' AND lease_id=%s "
        "RETURNING game_id",
        (state, error, now, game_id, parent_lease_id),
    ).fetchone()


def fail_parent(database, game_id, lease_id, error, now):
    return database.execute_native(
        "UPDATE game_analysis_jobs SET status='failed',lease_id=NULL,lease_expires_at=NULL,"
        "last_error=%s,updated_at=%s WHERE game_id=%s AND status='leased' AND lease_id=%s "
        "RETURNING game_id", (error, now, game_id, lease_id),
    ).fetchone()


def parent_exists(database, game_id):
    return database.execute_native(
        "SELECT 1 FROM game_analysis_jobs WHERE game_id=%s", (game_id,),
    ).fetchone()


def heartbeat_parent(database, game_id, lease_id, expires_at, now):
    return database.execute_native(
        "UPDATE game_analysis_jobs SET lease_expires_at=%s,updated_at=%s "
        "WHERE game_id=%s AND status='leased' AND lease_id=%s RETURNING game_id",
        (expires_at, now, game_id, lease_id),
    ).fetchone()


def release_parent(database, game_id, lease_id, now):
    return database.execute_native(
        "UPDATE game_analysis_jobs SET status='queued',lease_id=NULL,lease_expires_at=NULL,"
        "updated_at=%s WHERE game_id=%s AND status='leased' AND lease_id=%s RETURNING game_id",
        (now, game_id, lease_id),
    ).fetchone()


def retry_failed_parent(database, game_id, now):
    return database.execute_native(
        "UPDATE game_analysis_jobs SET status='queued',lease_id=NULL,lease_expires_at=NULL,"
        "last_error=NULL,updated_at=%s WHERE game_id=%s AND status='failed' "
        "RETURNING analysis_version", (now, game_id),
    ).fetchone()


def retry_failed_positions(database, game_id, version, now):
    database.execute_native(
        "UPDATE game_analysis_position_reports SET state='queued',attempts=0,"
        "lease_id=NULL,parent_lease_id=NULL,lease_expires_at=NULL,last_error=NULL,updated_at=%s "
        "WHERE game_id=%s AND analysis_version=%s AND state='failed'",
        (now, game_id, version),
    )


def mark_activity_queued(database, game_id, now):
    database.execute_native(
        "INSERT INTO background_activity(source,work_id,paused,phase,updated_at) "
        "VALUES('game_analysis',%s,0,'Queued',%s) "
        "ON CONFLICT(source,work_id) DO UPDATE SET paused=0,phase='Queued',"
        "completed_units=NULL,total_units=NULL,updated_at=excluded.updated_at",
        (game_id, now),
    )


def legacy_timeout_candidate(database):
    return database.execute_native(
        "SELECT game_id,last_error FROM game_analysis_jobs "
        "WHERE status='failed' AND last_error='Stockfish took too long' "
        "ORDER BY updated_at,game_id LIMIT 1 FOR UPDATE SKIP LOCKED"
    ).fetchone()


def requeue_legacy_timeout(database, game_id, now):
    database.execute_native(
        "UPDATE game_analysis_jobs SET status='queued',last_error=NULL,lease_id=NULL,"
        "lease_expires_at=NULL,updated_at=%s WHERE game_id=%s",
        (now, game_id),
    )


def legacy_network_candidate(database):
    return database.execute_native(
        "SELECT j.game_id,j.analysis_version,g.analysis_version AS published_version "
        "FROM game_analysis_jobs j JOIN imported_games g ON g.id=j.game_id "
        "WHERE j.status='complete' AND j.analysis_evidence_version<3 "
        "AND EXISTS(SELECT 1 FROM game_move_analysis move WHERE move.game_id=j.game_id "
        "AND move.network_version='nn-1c0000000000.nnue') "
        "ORDER BY j.updated_at,j.game_id LIMIT 1 FOR UPDATE OF j SKIP LOCKED"
    ).fetchone()


def requeue_legacy_network(database, game_id, version, now):
    database.execute_native(
        "UPDATE game_analysis_jobs SET status='queued',analysis_version=%s,"
        "analysis_evidence_version=3,lease_id=NULL,lease_expires_at=NULL,"
        "last_error=NULL,updated_at=%s WHERE game_id=%s AND status='complete'",
        (version, now, game_id),
    )
