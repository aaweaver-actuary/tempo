"""Fixed legacy Games queries on the composition root's borrowed connection."""


def job_status(database, job_id):
    return database.execute(
        "SELECT status FROM game_sync_jobs WHERE id=?",
        (job_id,),
    ).fetchone()


def provider_states(database):
    return database.execute(
        "SELECT * FROM game_sync_state ORDER BY provider"
    ).fetchall()


def latest_job(database):
    return database.execute(
        """SELECT * FROM game_sync_jobs
               ORDER BY CASE WHEN status IN ('queued','running','paused','retrying') THEN 0 ELSE 1 END,
                        created_at DESC LIMIT 1"""
    ).fetchone()
