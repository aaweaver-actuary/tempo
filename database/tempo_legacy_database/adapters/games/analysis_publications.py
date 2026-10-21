"""Bounded analysis publication storage on the caller's transaction.

Application lease/generation decisions, chess computations and durable task
orchestration stay in the existing command composition module.
"""


def lock_analysis_admission(database, game_id):
    return database.execute_native(
        "SELECT status,lease_id,analysis_version,analysis_evidence_version,idempotency_key "
        "FROM game_analysis_jobs WHERE game_id=%s FOR UPDATE", (game_id,),
    ).fetchone()


def published_result(database, game_id):
    return database.execute_native(
        "SELECT result_json FROM game_analysis_publications WHERE game_id=%s",
        (game_id,),
    ).fetchone()


def queue_publication(database, game_id, version, evidence_version, prepared_json, result_json, now):
    database.execute_native(
        "INSERT INTO game_analysis_publications("
        "game_id,analysis_version,analysis_evidence_version,prepared_json,next_ply,"
        "status,result_json,updated_at) "
        "VALUES(%s,%s,%s,%s,0,'queued',%s,%s) "
        "ON CONFLICT(game_id) DO UPDATE SET "
        "analysis_version=excluded.analysis_version,"
        "analysis_evidence_version=excluded.analysis_evidence_version,"
        "prepared_json=excluded.prepared_json,next_ply=0,status='queued',"
        "result_json=excluded.result_json,last_error=NULL,updated_at=excluded.updated_at",
        (game_id, version, evidence_version, prepared_json, result_json, now),
    )
    database.execute_native(
        "UPDATE game_analysis_jobs SET status='publishing',lease_id=NULL,"
        "lease_expires_at=NULL,updated_at=%s WHERE game_id=%s",
        (now, game_id),
    )


def publication_task(database, game_id):
    return database.execute_native(
        "SELECT id FROM background_tasks WHERE kind='game_analysis_publish' "
        "AND deduplication_key=%s", (game_id,),
    ).fetchone()


def lock_manual_admission(database, game_id):
    return database.execute_native(
        "SELECT status,lease_id,analysis_version,idempotency_key "
        "FROM game_analysis_jobs WHERE game_id=%s FOR UPDATE", (game_id,),
    ).fetchone()


def lock_manual_game(database, game_id):
    return database.execute_native(
        "SELECT color,start_fen,moves_json,analysis_version FROM imported_games "
        "WHERE id=%s FOR UPDATE", (game_id,),
    ).fetchone()


def add_manual_job(database, game_id, version, evidence_version, now):
    database.execute_native(
        "INSERT INTO game_analysis_jobs(game_id,analysis_version,"
        "analysis_evidence_version,status,updated_at) "
        "VALUES(%s,%s,%s,'publishing',%s)",
        (game_id, version, evidence_version, now),
    )


def update_manual_job(database, game_id, version, evidence_version, now):
    database.execute_native(
        "UPDATE game_analysis_jobs SET status='publishing',analysis_version=%s,"
        "analysis_evidence_version=%s,lease_id=NULL,lease_expires_at=NULL,"
        "last_error=NULL,updated_at=%s WHERE game_id=%s",
        (version, evidence_version, now, game_id),
    )


def task_lease(database, task_id):
    return database.execute_native(
        "SELECT generation,lease_token,state FROM background_tasks WHERE id=%s",
        (task_id,),
    ).fetchone()


def publication(database, game_id):
    return database.execute_native(
        "SELECT analysis_version,analysis_evidence_version,prepared_json,next_ply,"
        "status,result_json FROM game_analysis_publications WHERE game_id=%s",
        (game_id,),
    ).fetchone()


def lock_publication_cursor(database, game_id, version):
    return database.execute_native(
        "SELECT next_ply,status FROM game_analysis_publications "
        "WHERE game_id=%s AND analysis_version=%s FOR UPDATE",
        (game_id, version),
    ).fetchone()


def reset_staged_moves(database, game_id, version, start_ply, end_ply):
    database.execute_native(
        "DELETE FROM game_move_analysis_staged WHERE game_id=%s "
        "AND publication_generation=%s AND ply>=%s AND ply<%s",
        (game_id, version, start_ply, end_ply),
    )


def write_staged_moves(database, rows_json):
    database.execute_native(
        "INSERT INTO game_move_analysis_staged("
        "game_id,publication_generation,ply,eval_before_cp,eval_after_cp,loss_cp,label,"
        "depth,best_move_uci,principal_variation_json,mate_before,mate_after,"
        "engine_version,network_version,mover_color,is_player_move,actual_move_uci,position_fen) "
        "SELECT game_id,publication_generation,ply,eval_before_cp,eval_after_cp,loss_cp,label,"
        "depth,best_move_uci,principal_variation_json,mate_before,mate_after,"
        "engine_version,network_version,mover_color,is_player_move,actual_move_uci,position_fen "
        "FROM jsonb_to_recordset(%s::jsonb) AS item("
        "game_id text,publication_generation bigint,ply bigint,eval_before_cp bigint,"
        "eval_after_cp bigint,loss_cp bigint,label text,depth bigint,best_move_uci text,"
        "principal_variation_json text,mate_before bigint,mate_after bigint,"
        "engine_version text,network_version text,mover_color text,is_player_move bigint,"
        "actual_move_uci text,position_fen text)",
        (rows_json,),
    )


def write_staged_candidates(database, rows_json):
    database.execute_native(
        "INSERT INTO game_move_analysis_candidates_staged("
        "game_id,publication_generation,ply,rank,candidate_uci,score_cp,mate,"
        "score_text,principal_variation_json,depth,position_fen,engine_version,network_version) "
        "SELECT game_id,publication_generation,ply,rank,candidate_uci,score_cp,mate,"
        "score_text,principal_variation_json,depth,position_fen,engine_version,network_version "
        "FROM jsonb_to_recordset(%s::jsonb) AS item("
        "game_id text,publication_generation bigint,ply bigint,rank bigint,"
        "candidate_uci text,score_cp bigint,mate bigint,score_text text,"
        "principal_variation_json text,depth bigint,position_fen text,"
        "engine_version text,network_version text)",
        (rows_json,),
    )


def advance_publication_cursor(database, game_id, version, end_ply, now):
    database.execute_native(
        "UPDATE game_analysis_publications SET next_ply=%s,status='publishing',updated_at=%s "
        "WHERE game_id=%s AND analysis_version=%s",
        (end_ply, now, game_id, version),
    )


def lock_generation_job(database, game_id, version):
    return database.execute_native(
        "SELECT status,analysis_evidence_version FROM game_analysis_jobs "
        "WHERE game_id=%s AND analysis_version=%s FOR UPDATE", (game_id, version),
    ).fetchone()


def staged_move_count(database, game_id, version):
    return database.execute_native(
        "SELECT COUNT(*) FROM game_move_analysis_staged "
        "WHERE game_id=%s AND publication_generation=%s",
        (game_id, version),
    ).fetchone()[0]


def publish_game_generation(database, game_id, version, evidence_version, major_mistake_ply, missed_punishment_ply):
    database.execute_native(
        "UPDATE imported_games SET analysis_state='ready',analysis_version=%s,"
        "published_analysis_generation=%s,analysis_evidence_version=%s,"
        "major_mistake_ply=%s,missed_punishment_ply=%s WHERE id=%s",
        (version, version, evidence_version, major_mistake_ply, missed_punishment_ply, game_id),
    )


def complete_publication_job(database, game_id, evidence_version, idempotency_key, now):
    database.execute_native(
        "UPDATE game_analysis_jobs SET status='complete',analysis_evidence_version=%s,"
        "idempotency_key=%s,lease_id=NULL,lease_expires_at=NULL,last_error=NULL,"
        "updated_at=%s WHERE game_id=%s",
        (evidence_version, idempotency_key, now, game_id),
    )
    database.execute_native(
        "UPDATE game_analysis_publications SET status='complete',updated_at=%s "
        "WHERE game_id=%s", (now, game_id),
    )


def next_affected_repertoire(database, game_id, cursor):
    return database.execute_native(
        "SELECT DISTINCT repertoire_id FROM current_game_repertoire_matches game_repertoire_matches "
        "WHERE game_id=%s AND repertoire_id>%s ORDER BY repertoire_id LIMIT 1",
        (game_id, cursor),
    ).fetchone()


def lock_published_game(database, game_id):
    return database.execute_native(
        "SELECT analysis_version,published_analysis_generation FROM imported_games "
        "WHERE id=%s FOR UPDATE", (game_id,),
    ).fetchone()


def restart_derivation(database, game_id, now):
    database.execute_native(
        "INSERT INTO game_derivation_jobs(game_id,status,updated_at) "
        "VALUES(%s,'queued',%s) ON CONFLICT(game_id) DO UPDATE SET "
        "status='queued',last_error=NULL,"
        "derivation_version=game_derivation_jobs.derivation_version+1,"
        "completed_phases=0,next_attempt_at=NULL,updated_at=excluded.updated_at",
        (game_id, now),
    )


def derivation_version(database, game_id):
    return database.execute_native(
        "SELECT derivation_version FROM game_derivation_jobs WHERE game_id=%s",
        (game_id,),
    ).fetchone()


def current_repertoire_match(database, game_id, repertoire_id):
    return database.execute_native(
        "SELECT 1 FROM current_game_repertoire_matches game_repertoire_matches WHERE game_id=%s AND repertoire_id=%s",
        (game_id, repertoire_id),
    ).fetchone()
