"""Fixed legacy Games queries on the composition root's borrowed connection."""

from ..controls.claim_sql import claimable, control_order
from ..games.coordinator.storage import begin_claim
from ..games.analysis_worker.positions import record_position_error


def available(database, now):
    return database.execute_native(
        "SELECT 1 FROM game_analysis_jobs j JOIN imported_games g ON g.id=j.game_id "
                "LEFT JOIN background_activity control ON control.source='game_analysis' "
                "AND control.work_id=j.game_id "
                "WHERE (j.status='queued' OR (j.status='leased' AND j.lease_expires_at<%s)) "
                "AND g.rated=1 AND g.speed IN ('blitz','rapid','classical') "
                "AND COALESCE(control.paused,0)=0 LIMIT 1",
        (now,),
    ).fetchone()


def expire_leases(database, updated_at, expiration_cutoff):
    database.execute(
        """UPDATE game_analysis_jobs SET status='queued',lease_id=NULL,lease_expires_at=NULL,updated_at=?
               WHERE status='leased' AND lease_expires_at<?""",
        (updated_at, expiration_cutoff),
    )


def claimable_job(database, preferred_game_id):
    return database.execute(
        f"""SELECT j.game_id,j.analysis_version,j.analysis_evidence_version,
                      g.provider,g.username,g.played_at,g.color,g.start_fen,g.moves_json,
                      c.divergence_ply
               FROM game_analysis_jobs j
               JOIN imported_games g ON g.id=j.game_id
               LEFT JOIN current_repertoire_comparisons c ON c.game_id=g.id
               WHERE j.status='queued' AND g.rated=1 AND g.speed IN ('blitz','rapid','classical')
                 AND {claimable('game_analysis', 'j.game_id')}
                 AND (? IS NULL OR j.game_id=?)
               ORDER BY {control_order('game_analysis', 'j.game_id')}g.played_at DESC LIMIT 1""",
        (preferred_game_id, preferred_game_id),
    ).fetchone()


def lease_queued_job(database, lease_id, lease_expires_at, now, game_id):
    return database.execute(
        """UPDATE game_analysis_jobs SET status='leased',lease_id=?,lease_expires_at=?,attempts=attempts+1,updated_at=?
               WHERE game_id=? AND status='queued'""",
        (lease_id, lease_expires_at, now, game_id),
    ).rowcount


def mark_analyzing(database, game_id):
    database.execute(
        "UPDATE imported_games SET analysis_state='analyzing' WHERE id=?",
        (game_id,),
    )


def leased_parent(database, lease_id):
    return database.execute(
        """SELECT j.game_id,j.analysis_version,j.analysis_evidence_version,
                      j.lease_id,g.color,g.start_fen,g.moves_json,c.divergence_ply
               FROM game_analysis_jobs j JOIN imported_games g ON g.id=j.game_id
               LEFT JOIN current_repertoire_comparisons c ON c.game_id=j.game_id
               WHERE j.status='leased' AND j.lease_id=?""",
        (lease_id,),
    ).fetchone()


def timeout_repair_candidate(database):
    return database.execute(
        """SELECT game_id,last_error FROM game_analysis_jobs
               WHERE status='failed' AND last_error='Stockfish took too long'
               ORDER BY updated_at,game_id LIMIT 1"""
    ).fetchone()


def requeue_timeout(database, now, game_id):
    database.execute(
        """UPDATE game_analysis_jobs SET status='queued',last_error=NULL,
                   lease_id=NULL,lease_expires_at=NULL,updated_at=? WHERE game_id=?""",
        (now, game_id),
    )


def mark_pending(database, game_id):
    database.execute(
        "UPDATE imported_games SET analysis_state='pending' WHERE id=?",
        (game_id,),
    )


def network_repair_candidate(database):
    return database.execute(
        """SELECT j.game_id,j.analysis_version,g.analysis_version AS published_version
               FROM game_analysis_jobs j JOIN imported_games g ON g.id=j.game_id
               WHERE j.status='complete' AND j.analysis_evidence_version<3
                 AND EXISTS(SELECT 1 FROM game_move_analysis move
                            WHERE move.game_id=j.game_id
                              AND move.network_version='nn-1c0000000000.nnue')
               ORDER BY j.updated_at,j.game_id LIMIT 1"""
    ).fetchone()


def requeue_network(database, analysis_version, now, game_id):
    database.execute(
        """UPDATE game_analysis_jobs SET status='queued',analysis_version=?,
                   analysis_evidence_version=3,lease_id=NULL,lease_expires_at=NULL,
                   last_error=NULL,updated_at=? WHERE game_id=? AND status='complete'""",
        (analysis_version, now, game_id),
    )


def lease_state(database, game_id):
    return database.execute(
        "SELECT lease_id,status FROM game_analysis_jobs WHERE game_id=?",
        (game_id,),
    ).fetchone()


def fail_job(database, error, now, game_id):
    database.execute(
        """UPDATE game_analysis_jobs SET status='failed',lease_id=NULL,lease_expires_at=NULL,last_error=?,updated_at=? WHERE game_id=?""",
        (error, now, game_id),
    )


def mark_failed(database, game_id):
    database.execute(
        "UPDATE imported_games SET analysis_state='failed' WHERE id=?",
        (game_id,),
    )


def renew_lease(database, lease_expires_at, now, game_id, lease_id):
    return database.execute(
        """UPDATE game_analysis_jobs SET lease_expires_at=?,updated_at=?
               WHERE game_id=? AND status='leased' AND lease_id=?""",
        (lease_expires_at, now, game_id, lease_id),
    ).rowcount


def release_lease(database, now, game_id, lease_id):
    return database.execute(
        """UPDATE game_analysis_jobs SET status='queued',lease_id=NULL,
                      lease_expires_at=NULL,updated_at=?
               WHERE game_id=? AND status='leased' AND lease_id=?""",
        (now, game_id, lease_id),
    ).rowcount


def retry_failed(database, now, game_id):
    return database.execute(
        """UPDATE game_analysis_jobs SET status='queued',lease_id=NULL,lease_expires_at=NULL,last_error=NULL,updated_at=?
               WHERE game_id=? AND status='failed'""",
        (now, game_id),
    ).rowcount


def queue_activity(database, now, game_id):
    database.execute(
        """UPDATE background_activity SET phase='Queued',completed_units=NULL,
               total_units=NULL,updated_at=? WHERE source='game_analysis' AND work_id=?""",
        (now, game_id),
    )
