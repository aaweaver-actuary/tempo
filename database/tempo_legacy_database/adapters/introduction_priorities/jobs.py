"""Existing priority refresh row operations; the caller owns clocks and transactions."""
from tempo_legacy_database.adapters.controls.claim_sql import claimable, control_order


def known_generation(database, repertoire_id):
    return database.execute(
            'SELECT generation FROM repertoire_priority_jobs WHERE repertoire_id=? '
            'UNION ALL SELECT generation FROM repertoire_priority_publications WHERE repertoire_id=? LIMIT 1',
            (repertoire_id, repertoire_id),
        ).fetchone()


def enqueue_generation(database, repertoire_id, next_attempt_at, updated_at):
    return database.execute(
        """INSERT INTO repertoire_priority_jobs(
               repertoire_id,generation,status,attempts,next_attempt_at,last_error,updated_at
           ) VALUES(?,1,'queued',0,?,NULL,?)
           ON CONFLICT(repertoire_id) DO UPDATE SET
               generation=repertoire_priority_jobs.generation+1,
               status='queued',attempts=0,next_attempt_at=excluded.next_attempt_at,
               last_error=NULL,updated_at=excluded.updated_at""",
        (repertoire_id, next_attempt_at, updated_at),
    )


def generation(database, repertoire_id):
    return database.execute(
        "SELECT generation FROM repertoire_priority_jobs WHERE repertoire_id=?",
        (repertoire_id,),
    ).fetchone()[0]


def begin_claim(database):
    return database.execute("BEGIN IMMEDIATE")


def claim_candidate(database, now):
    return database.execute(
            f"""SELECT * FROM repertoire_priority_jobs
               WHERE status='queued' AND next_attempt_at<=?
               AND {claimable('priority', 'repertoire_priority_jobs.repertoire_id')}
               ORDER BY {control_order('priority', 'repertoire_priority_jobs.repertoire_id')}next_attempt_at,updated_at LIMIT 1""",
            (now,),
        ).fetchone()


def mark_running(database, now, repertoire_id, generation):
    return database.execute(
            """UPDATE repertoire_priority_jobs
               SET status='running',attempts=attempts+1,updated_at=?
               WHERE repertoire_id=? AND generation=? AND status='queued'""",
            (now, repertoire_id, generation),
        ).rowcount


def clear_pending_request(database, repertoire_id):
    return database.execute("UPDATE analysis_refresh_requests SET pending_since=NULL "
                             "WHERE kind='repertoire_priority' AND repertoire_id=?", (repertoire_id,))


def current_job(database, repertoire_id):
    return database.execute(
                    "SELECT generation,status FROM repertoire_priority_jobs WHERE repertoire_id=?",
                    (repertoire_id,),
                ).fetchone()


def complete_legacy_job(database, now, repertoire_id, generation):
    return database.execute(
                    """UPDATE repertoire_priority_jobs
                       SET status='complete',last_error=NULL,updated_at=?
                       WHERE repertoire_id=? AND generation=?""",
                    (now, repertoire_id, generation),
                )


def attempt_state(database, repertoire_id):
    return database.execute(
                    "SELECT generation,attempts FROM repertoire_priority_jobs WHERE repertoire_id=?",
                    (repertoire_id,),
                ).fetchone()


def retry_or_fail(database, status, next_attempt_at, error_message, updated_at, repertoire_id, generation):
    return database.execute(
                    """UPDATE repertoire_priority_jobs
                       SET status=?,next_attempt_at=?,last_error=?,updated_at=?
                       WHERE repertoire_id=? AND generation=?""",
                    (
                        status,
                        next_attempt_at,
                        error_message,
                        updated_at,
                        repertoire_id,
                        generation,
                    ),
                )


def complete_generation_job(database, now, repertoire_id, generation):
    return database.execute(
            """UPDATE repertoire_priority_jobs SET status='complete',last_error=NULL,updated_at=?
               WHERE repertoire_id=? AND generation=?""",
            (now, repertoire_id, generation),
        )
