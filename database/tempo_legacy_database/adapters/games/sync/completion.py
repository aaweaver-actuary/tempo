"""Existing provider/window receipt facts and sync completion writes."""


def lock_sync_job(database, job_id):
    return database.execute(
        "SELECT status,request_json,result_json FROM game_sync_jobs WHERE id=? FOR UPDATE",
        (job_id,),
    ).fetchone()


def unfinished_window(database, job_id):
    return database.execute(
        """SELECT 1 FROM game_sync_windows WHERE job_id=?
           AND status NOT IN ('complete','split') LIMIT 1""",
        (job_id,),
    ).fetchone()


def unfinished_record(database, job_id):
    return database.execute(
        """SELECT 1 FROM background_tasks WHERE kind='game_sync_record'
           AND deduplication_key LIKE ? AND state!='complete' LIMIT 1""",
        (f"{job_id}:%",),
    ).fetchone()


def provider_window_counts(database, job_id):
    return database.execute(
        """SELECT provider,SUM(fetched_count) fetched,SUM(filtered_count) filtered,
                  SUM(rejected_count) rejected,MIN(window_start_ms) earliest_ms
           FROM game_sync_windows WHERE job_id=? GROUP BY provider""",
        (job_id,),
    ).fetchall()


def save_provider_result(database, provider, username, cursor, now, result_json):
    database.execute(
        """INSERT INTO game_sync_state(provider,username,status,cursor,last_success_at,
                   last_error,retry_after,last_result_json)
               VALUES(?,?,'idle',?,?,NULL,NULL,?)
               ON CONFLICT(provider) DO UPDATE SET username=excluded.username,
                   status='idle',cursor=excluded.cursor,
                   last_success_at=excluded.last_success_at,last_error=NULL,
                   retry_after=NULL,last_result_json=excluded.last_result_json""",
        (provider, username, cursor, now, result_json),
    )


def complete_sync_job(database, job_id, result_json, now):
    database.execute(
        """UPDATE game_sync_jobs SET status='complete',result_json=?,error=NULL,
           completed_at=?,updated_at=? WHERE id=?""",
        (result_json, now, now, job_id),
    )
