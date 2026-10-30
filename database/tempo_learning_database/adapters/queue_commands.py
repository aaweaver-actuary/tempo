"""Foreground queue command storage and dialect-specific locks."""
from . import queue_application
from .postgres_store import PostgresConnection
from .queue_position_lock import lock_queue_date_for_position


def lock_attempt_card(database, entry_id: int) -> None:
    """Follow review's card-before-queue lock order for retained commands."""
    if not isinstance(database, PostgresConnection):
        return
    entry = database.execute(
        "SELECT card_id FROM daily_queue WHERE id=?", (entry_id,)
    ).fetchone()
    if entry:
        database.execute_native(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (f"tempo:card-edit:{entry['card_id']}",),
        )
        database.execute(
            "SELECT id FROM cards WHERE id=? FOR UPDATE", (entry["card_id"],)
        )


def available_attempt(database, queue_date, entry_id, identified, miss_reason):
    lock_clause = (
        " FOR UPDATE OF q,c" if isinstance(database, PostgresConnection) else ""
    )
    return database.execute(
        f"""{queue_application.available_attempt_sql(miss_reason)}
              {"AND q.id=?" if identified else ""}
            ORDER BY q.position,q.id LIMIT 1{lock_clause}""",
        (queue_date, entry_id) if identified else (queue_date,),
    ).fetchone()


def queue_head(database, queue_date, miss_reason):
    return database.execute(
        f"{queue_application.available_attempt_sql(miss_reason)} ORDER BY q.position,q.id LIMIT 1",
        (queue_date,),
    ).fetchone()


def issued_origin(database, entry_id, card_id, revision):
    return database.execute(
        "SELECT 1 FROM queue_attempt_origins WHERE queue_entry_id=? AND card_id=? "
        "AND revision=? AND issued_as_head=1",
        (entry_id, card_id, revision),
    ).fetchone()


def lock_issue_queue_position(database, queue_date):
    if isinstance(database, PostgresConnection):
        lock_queue_date_for_position(database, queue_date)


def issued_head_flag(database, entry_id, card_id, revision):
    return database.execute("SELECT issued_as_head FROM queue_attempt_origins WHERE queue_entry_id=? AND card_id=? AND revision=?",
        (entry_id, card_id, revision)).fetchone()


def mark_head_issued(database, entry_id, card_id, revision):
    return database.execute(
        "UPDATE queue_attempt_origins SET issued_as_head=1 "
        "WHERE queue_entry_id=? AND card_id=? AND revision=?",
        (entry_id, card_id, revision),
    ).rowcount


def mark_attempt_failed(database, entry_id):
    return database.execute(
        "UPDATE daily_queue SET attempt_failed=1 WHERE id=? AND status='queued'",
        (entry_id,),
    ).rowcount


def bury_card_entries(database, queue_date, entry_id):
    database.execute(
        """UPDATE daily_queue SET status='buried'
           WHERE queue_date=? AND status!='complete'
             AND card_id=(SELECT card_id FROM daily_queue WHERE id=?)""",
        (queue_date, entry_id),
    )


def current_refresh_task(database):
    # Every task/projection write locks the task first, including timeout recovery.
    current_task_lock_sql = """SELECT id,state,payload_json,last_error FROM background_tasks
           WHERE kind='daily_queue' AND deduplication_key='current' FOR UPDATE"""
    active_task = database.execute(current_task_lock_sql).fetchone()
    if active_task is None and isinstance(database, PostgresConnection):
        # Serialize first ensures without owning a projection while waiting for
        # task creation. Recheck after the creator's transaction has committed.
        database.execute_native(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("tempo:daily-queue:current:ensure",),
        )
        active_task = database.execute(current_task_lock_sql).fetchone()
    return active_task


def refresh_projection(database, queue_date, has_active_task):
    projection_lock_clause = " FOR UPDATE" if has_active_task else ""
    return database.execute(
        """SELECT projection.state,projection.refresh_pending,
                  (SELECT day.membership_hash FROM daily_queue_days AS day
                   WHERE day.queue_date=projection.queue_date) AS presentation_membership_hash,
                  EXISTS(SELECT 1 FROM daily_queue AS entry
                         WHERE entry.queue_date=projection.queue_date AND entry.status='queued') AS has_queued_entries
           FROM queue_projections AS projection WHERE projection.queue_date=?"""
        + projection_lock_clause,
        (queue_date,),
    ).fetchone()


def retain_refreshing_projection(database, queue_date, last_error):
    database.execute(
        """INSERT INTO queue_projections(queue_date,state,generation,refresh_pending,last_error)
               VALUES(?,'refreshing',0,1,?) ON CONFLICT(queue_date) DO UPDATE SET
               state='refreshing',refresh_pending=1,last_error=excluded.last_error""",
        (queue_date, last_error),
    )
