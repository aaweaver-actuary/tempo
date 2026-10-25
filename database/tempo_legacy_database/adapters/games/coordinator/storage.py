"""Fixed legacy worker queries, borrowing their original transaction."""

from typing import Callable

from ...controls.claim_sql import claimable, control_order


def active_sync_job(database):
    return database.execute(
        """SELECT id FROM game_sync_jobs
               WHERE status IN ('queued','running','paused','retrying')
               ORDER BY created_at LIMIT 1"""
    ).fetchone()


def insert_sync_job(database, job_id, request_json, now):
    database.execute(
        """INSERT INTO game_sync_jobs(id,request_json,status,created_at,updated_at)
               VALUES(?,?,'queued',?,?)""",
        (job_id, request_json, now, now),
    )


def begin_claim(database):
    database.execute("BEGIN IMMEDIATE")


def claimable_sync_job(database):
    return database.execute(
        f"""SELECT * FROM game_sync_jobs WHERE status IN ('queued','retrying')
               AND {claimable('sync', 'game_sync_jobs.id')}
               ORDER BY {control_order('sync', 'game_sync_jobs.id')}created_at LIMIT 1"""
    ).fetchone()


def mark_sync_running(database, job_id, now):
    return database.execute(
        """UPDATE game_sync_jobs SET status='running',started_at=COALESCE(started_at,?),updated_at=?
               WHERE id=? AND status IN ('queued','retrying')""",
        (now, now, job_id),
    ).rowcount


def complete_sync_job(database, job_id, result_json, now):
    database.execute(
        """UPDATE game_sync_jobs SET status='complete',result_json=?,error=NULL,
                      completed_at=?,updated_at=? WHERE id=?""",
        (result_json, now, now, job_id),
    )


def fail_sync_job(database, job_id, error, now):
    database.execute(
        """UPDATE game_sync_jobs SET status='failed',error=?,completed_at=?,updated_at=?
               WHERE id=?""",
        (error, now, now, job_id),
    )


def queue_derivation(database, game_id, now):
    database.execute(
        """INSERT INTO game_derivation_jobs(game_id,status,updated_at)
               VALUES(?,'queued',?)
               ON CONFLICT(game_id) DO UPDATE SET status='queued',last_error=NULL,
                   derivation_version=game_derivation_jobs.derivation_version+1,
                   completed_phases=0,next_attempt_at=NULL,updated_at=excluded.updated_at""",
        (game_id, now),
    )


def matching_repertoire_ids(database, game_id):
    return [row[0] for row in database.execute(
        "SELECT DISTINCT repertoire_id FROM current_game_repertoire_matches game_repertoire_matches WHERE game_id=?",
        (game_id,),
    )]


def claimable_derivation(database, current_time: Callable[[], str]):
    """Evaluate the existing claim fragments before the application clock."""
    return database.execute(
        f"""SELECT game_id FROM game_derivation_jobs WHERE status='queued'
               AND (next_attempt_at IS NULL OR next_attempt_at<=?)
               AND {claimable('derivation', 'game_derivation_jobs.game_id')}
               ORDER BY {control_order('derivation', 'game_derivation_jobs.game_id')}updated_at LIMIT 1"""
            , (current_time(),)
    ).fetchone()


def mark_derivation_running(database, game_id, now):
    return database.execute(
        """UPDATE game_derivation_jobs SET status='running',phase='claimed',attempts=attempts+1,updated_at=?
               WHERE game_id=? AND status='queued'""",
        (now, game_id),
    ).rowcount


def set_derivation_phase(database, game_id, phase, now):
    database.execute(
        "UPDATE game_derivation_jobs SET phase=?,updated_at=? WHERE game_id=? AND status='running'",
        (phase, now, game_id),
    )


def derivation_version(database, game_id):
    return database.execute("SELECT derivation_version FROM game_derivation_jobs WHERE game_id=?", (game_id,)).fetchone()


def derivation_phase_status(database, game_id):
    return database.execute(
        "SELECT completed_phases,status FROM game_derivation_jobs WHERE game_id=?", (game_id,)
    ).fetchone()


def advance_derivation_phase(database, game_id, next_phase_index, phase_index, now):
    return database.execute(
        """UPDATE game_derivation_jobs SET completed_phases=?,updated_at=?
                   WHERE game_id=? AND status='running' AND completed_phases=?""",
        (next_phase_index, now, game_id, phase_index),
    ).rowcount


def derivation_pause_state(database, game_id):
    return database.execute(
        "SELECT paused FROM background_activity WHERE source='derivation' AND work_id=?",
        (game_id,),
    ).fetchone()


def pause_running_derivation(database, game_id, now):
    database.execute(
        """UPDATE game_derivation_jobs SET status='queued',phase='paused',updated_at=?
               WHERE game_id=? AND status='running'""",
        (now, game_id),
    )


def ensure_running_derivation(database, game_id, now):
    database.execute(
        """INSERT OR IGNORE INTO game_derivation_jobs(game_id,status,updated_at)
                       VALUES(?,'running',?)""",
        (game_id, now),
    )


def resume_unpaused_derivation(database, game_id, now):
    database.execute(
        """UPDATE game_derivation_jobs SET status='running',updated_at=?
                       WHERE game_id=? AND status='queued' AND NOT EXISTS(
                           SELECT 1 FROM background_activity a
                           WHERE a.source='derivation' AND a.work_id=? AND a.paused=1
                       )""",
        (now, game_id, game_id),
    )


def derivation_status(database, game_id):
    return database.execute(
        "SELECT status FROM game_derivation_jobs WHERE game_id=?", (game_id,)
    ).fetchone()


def complete_running_derivation(database, game_id, now):
    return database.execute(
        """UPDATE game_derivation_jobs SET status='complete',phase=NULL,last_error=NULL,next_attempt_at=NULL,updated_at=?
                       WHERE game_id=? AND status='running'""",
        (now, game_id),
    ).rowcount


def derivation_attempts(database, game_id):
    return database.execute(
        "SELECT attempts FROM game_derivation_jobs WHERE game_id=?",
        (game_id,),
    ).fetchone()


def retry_derivation(database, game_id, error, retry_at, now):
    database.execute(
        """UPDATE game_derivation_jobs SET status='queued',phase=NULL,last_error=?,next_attempt_at=?,updated_at=?
                               WHERE game_id=?""",
        (error, retry_at, now, game_id),
    )


def fail_derivation(database, game_id, error, now):
    database.execute(
        """UPDATE game_derivation_jobs SET status='failed',phase=NULL,last_error=?,updated_at=?
                       WHERE game_id=?""",
        (error, now, game_id),
    )


def position_source_game(database, game_id):
    return database.execute(
        "SELECT start_fen,moves_json FROM imported_games WHERE id=?", (game_id,)
    ).fetchone()


def delete_game_positions(database, game_id):
    database.execute("DELETE FROM game_position_occurrences WHERE game_id=?", (game_id,))


def insert_position_values(database, occurrences):
    database.executemany(
        """INSERT INTO game_position_occurrences(game_id,ply,fen_key,move_uci)
               VALUES(?,?,?,?)""",
        occurrences,
    )


def requeue_interrupted_syncs(database, now):
    database.execute(
        """UPDATE game_sync_jobs SET status='queued',started_at=NULL,updated_at=?
               WHERE status IN ('running','paused')""",
        (now,),
    )


def requeue_interrupted_derivations(database, now):
    database.execute(
        """UPDATE game_derivation_jobs SET status='queued',phase=NULL,updated_at=?
               WHERE status='running'""",
        (now,),
    )


def requeue_interrupted_coverage(database, now):
    database.execute(
        """UPDATE repertoire_coverage_nodes SET explorer_status='queued',updated_at=?
               WHERE explorer_status='running'""",
        (now,),
    )


def requeue_interrupted_integrity_jobs(database, now):
    database.execute(
        """UPDATE repertoire_integrity_jobs SET status='queued',updated_at=?
               WHERE status IN ('running','finalizing')""",
        (now,),
    )


def requeue_interrupted_integrity_state(database):
    database.execute(
        """UPDATE repertoire_integrity_state SET scan_status='queued'
               WHERE scan_status IN ('running','retrying')"""
    )


