"""Analysis progress and manual-retry rows on the caller's locked transaction."""


def analysis_lease_for_update(database, work_id: str):
    return database.execute_native(
        "SELECT analysis_version,analysis_evidence_version,status,lease_id "
        "FROM game_analysis_jobs WHERE game_id=%s FOR UPDATE", (work_id,),
    ).fetchone()


def save_analysis_progress(database, work_id: str, generation: str, phase: str,
                           completed: int, total: int, updated_at: str) -> None:
    database.execute_native(
        "INSERT INTO background_activity(source,work_id,generation_key,phase,completed_units,total_units,updated_at) "
        "VALUES('game_analysis',%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(source,work_id) DO UPDATE SET generation_key=excluded.generation_key,"
        "phase=excluded.phase,completed_units=excluded.completed_units,"
        "total_units=excluded.total_units,updated_at=excluded.updated_at",
        (work_id, generation, phase, completed, total, updated_at),
    )


def failed_task_for_update(database, task_id: str):
    return database.execute_native(
        "SELECT * FROM background_tasks WHERE id=%s AND state='failed' FOR UPDATE", (task_id,),
    ).fetchone()


def reset_failed_task(database, task_id: str, now: str) -> None:
    database.execute_native(
        "UPDATE background_tasks SET state='queued',phase='queued',attempt_count=0,"
        "transaction_timeout_count=0,transaction_timeout_checkpoint=NULL,"
        "next_attempt_at=%s,lease_token=NULL,lease_expires_at=NULL,last_error=NULL,"
        "completed_at=NULL,updated_at=%s WHERE id=%s", (now, now, task_id),
    )


def record_retry_activity(database, task_id: str, now: str) -> None:
    database.execute_native(
        "INSERT INTO background_activity(source,work_id,paused,phase,updated_at) "
        "VALUES('durable',%s,0,'Queued',%s) ON CONFLICT(source,work_id) DO UPDATE SET "
        "paused=0,phase='Queued',completed_units=NULL,total_units=NULL,updated_at=excluded.updated_at",
        (task_id, now),
    )


def record_manual_retry(database, task_id: str, generation: int, now: str):
    database.execute_native(
        "INSERT INTO background_task_events(task_id,generation,event,phase,created_at) "
        "VALUES(%s,%s,'manual_retry','queued',%s)", (task_id, generation, now),
    )
    return database.execute_native(
        "SELECT * FROM background_tasks WHERE id=%s", (task_id,),
    ).fetchone()
