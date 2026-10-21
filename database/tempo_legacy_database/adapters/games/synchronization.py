"""Provider-sync reads, locks and writes on the existing command transaction."""


def lock_provider_state(database, provider):
    return database.execute(
        "SELECT username,retry_after FROM game_sync_state WHERE provider=? FOR UPDATE",
        (provider,),
    ).fetchone()


def clear_provider_cursor(database, provider):
    database.execute(
        """UPDATE game_sync_state SET cursor=NULL,last_success_at=NULL,
                   retry_after=NULL WHERE provider=?""",
        (provider,),
    )


def mark_provider_syncing(database, provider, username, created_at):
    database.execute(
        """INSERT INTO game_sync_state(provider,username,status,last_started_at,last_error)
               VALUES(?,?,'syncing',?,NULL)
               ON CONFLICT(provider) DO UPDATE SET username=excluded.username,
                   status='syncing',last_started_at=excluded.last_started_at,last_error=NULL""",
        (provider, username, created_at),
    )


def newest_game_played_at(database, provider, username):
    return database.execute(
        """SELECT MAX(played_at) FROM imported_games
                   WHERE provider=? AND lower(username)=lower(?)""",
        (provider, username),
    ).fetchone()[0]


def add_sync_window(database, window_id, job_id, provider, window_kind,
                    window_start_ms, window_end_ms, created_at):
    database.execute(
        """INSERT INTO game_sync_windows(
                   id,job_id,provider,window_kind,window_start_ms,window_end_ms,
                   created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""",
        (window_id, job_id, provider, window_kind, window_start_ms,
         window_end_ms, created_at, created_at),
    )


def lock_sync_admission(database):
    # Preserve the absent-active-job serialization, on the caller's transaction.
    database.raw.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        ("tempo:game-sync-admission",),
    )


def active_sync_job(database):
    return database.execute(
        """SELECT id,status,request_json,created_at FROM game_sync_jobs
           WHERE status IN ('queued','running','paused','retrying')
           ORDER BY created_at LIMIT 1 FOR UPDATE""",
    ).fetchone()


def existing_job_window(database, job_id):
    return database.execute(
        "SELECT 1 FROM game_sync_windows WHERE job_id=? LIMIT 1", (job_id,),
    ).fetchone()


def mark_job_queued(database, job_id, now):
    database.execute(
        "UPDATE game_sync_jobs SET status='queued',updated_at=? WHERE id=?",
        (now, job_id),
    )


def add_sync_job(database, job_id, request_json, created_at):
    database.execute(
        """INSERT INTO game_sync_jobs(id,request_json,status,created_at,updated_at)
           VALUES(?,?,'queued',?,?)""",
        (job_id, request_json, created_at, created_at),
    )
