"""Existing foreground queue locks and fixed queries on the caller transaction."""

from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from tempo_legacy_database.adapters.http_composition.availability import available_attempt_sql
from tempo_legacy_database.adapters.queue_refresh.foreground import _QUEUE_REFRESH_PROJECTION_SQL


def is_postgres_connection(database):
    return isinstance(database, PostgresConnection)


def study_timezone(database):
    row = database.execute('SELECT timezone FROM settings WHERE id=1').fetchone()
    return row[0] if row else None


def warm_queue_refresh_sql(warm_completion_sql):
    from tempo_legacy_database.adapters.postgres_store import postgres_sql

    warm_completion_sql()
    postgres_sql(_QUEUE_REFRESH_PROJECTION_SQL)


def _lock_attempt_card(database, entry_id: int) -> None:
    """Follow review's card-before-queue lock order for retained commands."""
    if not isinstance(database, PostgresConnection):
        return
    entry = database.execute("SELECT card_id FROM daily_queue WHERE id=?", (entry_id,)).fetchone()
    if entry:
        database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                                (f"tempo:card-edit:{entry['card_id']}",))
        database.execute("SELECT id FROM cards WHERE id=? FOR UPDATE", (entry['card_id'],))


def prepare_available_attempt(database, identified, entry_id, miss_reason):
    lock_clause = " FOR UPDATE OF q,c" if isinstance(database, PostgresConnection) else ""
    execute = database.execute
    statement = f"""{available_attempt_sql(miss_reason)}
              {"AND q.id=?" if identified else ""}
            ORDER BY q.position,q.id LIMIT 1{lock_clause}"""

    def read_day(queue_date):
        return execute(
            statement,
            (queue_date, entry_id) if identified else (queue_date,),
        ).fetchone()

    return read_day


def prepare_queue_head(database, miss_reason):
    execute = database.execute
    statement = f"{available_attempt_sql(miss_reason)} ORDER BY q.position,q.id LIMIT 1"

    def read_day(queue_date):
        return execute(statement, (queue_date,)).fetchone()

    return read_day


def issued_origin(database, entry_id, card_id, expected_revision):
    return database.execute(
            "SELECT 1 FROM queue_attempt_origins WHERE queue_entry_id=? AND card_id=? "
            "AND revision=? AND issued_as_head=1",
            (entry_id, card_id, expected_revision),
        ).fetchone()


def issued_origin_day(database, entry_id, card_id, expected_revision):
    row = database.execute("""SELECT queue_date FROM queue_attempt_origins
        WHERE queue_entry_id=? AND card_id=? AND revision=? AND issued_as_head=1""",
        (entry_id, card_id, expected_revision)).fetchone()
    return row[0] if row else None


def stamp_issued_head(database, entry_id, card_id, expected_revision):
    from tempo_legacy_database.adapters.study.issuance import stamp_issued_head as stamp
    return stamp(database, entry_id, card_id, expected_revision)


def mark_attempt_failed(database, entry_id):
    return database.execute(
        "UPDATE daily_queue SET attempt_failed=1 WHERE id=? AND status='queued'",
        (entry_id,),
    ).rowcount


def bury_attempt_card(database, queue_date, entry_id):
    database.execute(
        """UPDATE daily_queue SET status='buried'
           WHERE (queue_date=? OR id=?) AND status!='complete'
             AND card_id=(SELECT card_id FROM daily_queue WHERE id=?)""",
        (queue_date, entry_id, entry_id),
    )
    # Rollover preserves real issued boards. Burying that board retires its old
    # row AND retains today's exclusion even when it had no current-day row.
    database.execute("""INSERT INTO daily_queue(queue_date,card_id,cycle,position,status)
        SELECT ?,original.card_id,
            (SELECT COALESCE(MAX(cycle),-1)+1 FROM daily_queue q
             WHERE q.queue_date=? AND q.card_id=original.card_id),
            (SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue q WHERE q.queue_date=?),'buried'
        FROM daily_queue original WHERE original.id=? AND original.queue_date<>?
          AND NOT EXISTS(SELECT 1 FROM daily_queue held
              WHERE held.queue_date=? AND held.card_id=original.card_id AND held.status='buried')""",
        (queue_date,queue_date,queue_date,entry_id,queue_date,queue_date))


def lock_current_queue_task(database):
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


def current_projection(database, queue_date, active_task):
    projection_lock_clause = " FOR UPDATE" if active_task else ""
    return database.execute(
        """SELECT projection.state,projection.refresh_pending,
                  (SELECT day.membership_hash FROM daily_queue_days AS day
                   WHERE day.queue_date=projection.queue_date) AS presentation_membership_hash,
                  EXISTS(SELECT 1 FROM daily_queue AS entry
                         WHERE entry.queue_date=projection.queue_date AND entry.status='queued') AS has_queued_entries
           FROM queue_projections AS projection WHERE projection.queue_date=?""" + projection_lock_clause,
        (queue_date,),
    ).fetchone()


def mark_active_refresh(database, queue_date, last_error):
    database.execute(
            """INSERT INTO queue_projections(queue_date,state,generation,refresh_pending,last_error)
               VALUES(?,'refreshing',0,1,?) ON CONFLICT(queue_date) DO UPDATE SET
               state='refreshing',refresh_pending=1,last_error=excluded.last_error""",
            (queue_date, last_error),
        )
