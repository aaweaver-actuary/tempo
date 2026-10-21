"""Fixed legacy publication/defense queries on the caller's borrowed connection."""

from ..games.coordinator.storage import begin_claim
from ..games.mutations import affected_repertoire_ids


def game_source(database, game_id):
    return database.execute(
        "SELECT color,start_fen,moves_json FROM imported_games WHERE id=?",
        (game_id,),
    ).fetchone()


def analysis_job(database, game_id):
    return database.execute(
        "SELECT * FROM game_analysis_jobs WHERE game_id=?",
        (game_id,),
    ).fetchone()


def major_mistake(database, game_id):
    return database.execute(
        "SELECT major_mistake_ply FROM imported_games WHERE id=?",
        (game_id,),
    ).fetchone()


def missed_punishment(database, game_id):
    return database.execute(
        "SELECT missed_punishment_ply FROM imported_games WHERE id=?",
        (game_id,),
    ).fetchone()


def current_job(database, game_id):
    return database.execute(
        "SELECT status,lease_id,idempotency_key FROM game_analysis_jobs WHERE game_id=?",
        (game_id,),
    ).fetchone()


def delete_analysis(database, game_id):
    database.execute(
        "DELETE FROM game_move_analysis WHERE game_id=?",
        (game_id,),
    )


def delete_candidates(database, game_id):
    database.execute(
        "DELETE FROM game_move_analysis_candidates WHERE game_id=?",
        (game_id,),
    )


def insert_analysis(database, analysis_rows):
    database.executemany(
        """INSERT INTO game_move_analysis(
                game_id,ply,eval_before_cp,eval_after_cp,loss_cp,label,depth,best_move_uci,
                principal_variation_json,mate_before,mate_after,engine_version,network_version,
                mover_color,is_player_move,actual_move_uci,position_fen
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        analysis_rows,
    )


def insert_candidates(database, candidate_rows):
    database.executemany(
        """INSERT INTO game_move_analysis_candidates(
                   game_id,ply,rank,candidate_uci,score_cp,mate,score_text,
                   principal_variation_json,depth,position_fen,engine_version,network_version
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        candidate_rows,
    )


def publish_game(database, evidence_version, major_mistake_ply, missed_punishment_ply, game_id):
    database.execute(
        """UPDATE imported_games SET analysis_state='ready',analysis_version=analysis_version+1,
                   analysis_evidence_version=?,major_mistake_ply=?,missed_punishment_ply=? WHERE id=?""",
        (evidence_version, major_mistake_ply, missed_punishment_ply, game_id),
    )


def mark_superseded_cards(database, game_id):
    database.execute(
        """UPDATE cards SET pending_validation=1 WHERE id IN (
                 SELECT card_id FROM threat_training_candidates
                 WHERE game_id=? AND card_id IS NOT NULL AND analysis_version != (
                    SELECT analysis_version FROM imported_games WHERE id=?))""",
        (game_id, game_id),
    )


def supersede_candidates(database, now, game_id):
    database.execute(
        """UPDATE threat_training_candidates SET superseded_at=COALESCE(superseded_at,?)
               WHERE game_id=? AND analysis_version != (
                    SELECT analysis_version FROM imported_games WHERE id=?)""",
        (now, game_id, game_id),
    )


def complete_job(database, game_id, analysis_version, evidence_version, idempotency_key, now):
    database.execute(
        """INSERT INTO game_analysis_jobs(game_id,analysis_version,analysis_evidence_version,status,idempotency_key,updated_at)
               VALUES(?,?,?,'complete',?,?)
               ON CONFLICT(game_id) DO UPDATE SET analysis_version=excluded.analysis_version,
               analysis_evidence_version=excluded.analysis_evidence_version,status='complete',
               lease_id=NULL,lease_expires_at=NULL,idempotency_key=excluded.idempotency_key,last_error=NULL,updated_at=excluded.updated_at""",
        (game_id, analysis_version, evidence_version, idempotency_key, now),
    )


def published_version(database, game_id):
    return database.execute(
        "SELECT analysis_version FROM imported_games WHERE id=?",
        (game_id,),
    ).fetchone()
