"""Existing foreground queue locks and fixed queries on the caller transaction."""
from tempo_storage.postgres import PostgresConnection
from tempo_storage.http_composition.availability import available_attempt_sql, RETIRED_QUEUE_PREDICATE
from tempo_storage.queue_refresh.foreground import _QUEUE_REFRESH_PROJECTION_SQL

def study_timezone(database):
    row = database.execute('SELECT timezone FROM settings WHERE id=1').fetchone()
    return row[0] if row else None

def _lock_attempt_card(database, entry_id: int) -> None:
    """Follow review's card-before-queue lock order for retained commands."""
    entry = database.execute('SELECT card_id FROM daily_queue WHERE id=%s', (entry_id,)).fetchone()
    if entry:
        database.execute_native('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (f"tempo:card-edit:{entry['card_id']}",))
        database.execute('SELECT id FROM cards WHERE id=%s FOR UPDATE', (entry['card_id'],))

def prepare_available_attempt(database, identified, entry_id, miss_reason):
    lock_clause = ' FOR UPDATE OF q,c'
    execute = database.execute
    statement = f"{available_attempt_sql(miss_reason)}\n              {('AND q.id=%s' if identified else '')}\n            ORDER BY q.position,q.id LIMIT 1{lock_clause}"

    def read_day(queue_date):
        return execute(statement, (queue_date, entry_id) if identified else (queue_date,)).fetchone()
    return read_day

def prepare_queue_head(database, miss_reason):
    execute = database.execute
    statement = f'{available_attempt_sql(miss_reason)}{RETIRED_QUEUE_PREDICATE} ORDER BY q.position,q.id LIMIT 1'

    def read_day(queue_date):
        return execute(statement, (queue_date,)).fetchone()
    return read_day

def retired_issuance_target(database, entry_id, card_id, revision):
    return database.execute('SELECT 1 FROM daily_queue q JOIN cards c ON c.id=q.card_id\n           WHERE q.id=%s AND c.id=%s AND c.revision=%s AND NOT (1=1 ' + RETIRED_QUEUE_PREDICATE + ')', (entry_id, card_id, revision)).fetchone() is not None

def issued_origin(database, entry_id, card_id, expected_revision):
    return database.execute('SELECT 1 FROM queue_attempt_origins WHERE queue_entry_id=%s AND card_id=%s AND revision=%s AND issued_as_head=1', (entry_id, card_id, expected_revision)).fetchone()

def issued_origin_day(database, entry_id, card_id, expected_revision):
    row = database.execute('SELECT queue_date FROM queue_attempt_origins\n        WHERE queue_entry_id=%s AND card_id=%s AND revision=%s AND issued_as_head=1', (entry_id, card_id, expected_revision)).fetchone()
    return row[0] if row else None

def stamp_issued_head(database, entry_id, card_id, expected_revision):
    from tempo_storage.study.issuance import stamp_issued_head as stamp
    return stamp(database, entry_id, card_id, expected_revision)

def mark_attempt_failed(database, entry_id):
    return database.execute("UPDATE daily_queue SET attempt_failed=1 WHERE id=%s AND status='queued'", (entry_id,)).rowcount

def bury_attempt_card(database, queue_date, entry_id):
    database.execute("UPDATE daily_queue SET status='buried'\n           WHERE (queue_date=%s OR id=%s) AND status!='complete'\n             AND card_id=(SELECT card_id FROM daily_queue WHERE id=%s)", (queue_date, entry_id, entry_id))
    database.execute("INSERT INTO daily_queue(queue_date,card_id,cycle,position,status)\n        SELECT %s,original.card_id,\n            (SELECT COALESCE(MAX(cycle),-1)+1 FROM daily_queue q\n             WHERE q.queue_date=%s AND q.card_id=original.card_id),\n            (SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue q WHERE q.queue_date=%s),'buried'\n        FROM daily_queue original WHERE original.id=%s AND original.queue_date<>%s\n          AND NOT EXISTS(SELECT 1 FROM daily_queue held\n              WHERE held.queue_date=%s AND held.card_id=original.card_id AND held.status='buried')", (queue_date, queue_date, queue_date, entry_id, queue_date, queue_date))

def lock_current_queue_task(database, queue_date=None):
    # Always acquire the singleton first; the per-day task, when present, is
    # locked before its projection just as it is by a queue worker.
    import json
    current_task_lock_sql = "SELECT id,state,payload_json,last_error FROM background_tasks WHERE kind='daily_queue' AND deduplication_key='current' FOR UPDATE"
    active_task = database.execute(current_task_lock_sql).fetchone()
    if active_task is None:
        database.execute_native('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('tempo:daily-queue:current:ensure',))
        active_task = database.execute(current_task_lock_sql).fetchone()
    if queue_date and active_task:
        day = json.loads(active_task["payload_json"]).get("queue_date")
        if day and day != queue_date:
            return database.execute(
                "SELECT id,state,payload_json,last_error FROM background_tasks WHERE kind='daily_queue' AND deduplication_key=%s FOR UPDATE",
                ("current-day:" + queue_date,),
            ).fetchone()
    return active_task

def current_projection(database, queue_date, active_task):
    projection_lock_clause = ' FOR UPDATE' if active_task else ''
    return database.execute("SELECT projection.state,projection.refresh_pending,\n                  (SELECT day.membership_hash FROM daily_queue_days AS day\n                   WHERE day.queue_date=projection.queue_date) AS presentation_membership_hash,\n                  EXISTS(SELECT 1 FROM daily_queue AS entry\n                         WHERE entry.queue_date=projection.queue_date AND entry.status='queued') AS has_queued_entries\n           FROM queue_projections AS projection WHERE projection.queue_date=%s" + projection_lock_clause, (queue_date,)).fetchone()

def mark_active_refresh(database, queue_date, last_error):
    database.execute("INSERT INTO queue_projections(queue_date,state,generation,refresh_pending,last_error)\n               VALUES(%s,'refreshing',0,1,%s) ON CONFLICT(queue_date) DO UPDATE SET\n               state='refreshing',refresh_pending=1,last_error=excluded.last_error", (queue_date, last_error))

def issuance_owner_repertoires(database, entry_id, card_id, expected_revision):
    """Read retained admission owners on the caller transaction, in fence order."""
    return database.execute_native('SELECT DISTINCT r.repertoire_id FROM (\n            SELECT o.admission_repertoire_id repertoire_id FROM queue_attempt_origins o\n             WHERE o.queue_entry_id=%s AND o.card_id=%s AND o.revision=%s\n               AND o.admission_repertoire_id IS NOT NULL\n            UNION SELECT c.repertoire_id FROM cards c JOIN queue_attempt_origins o ON o.card_id=c.id\n             WHERE o.queue_entry_id=%s AND o.card_id=%s AND o.revision=%s AND o.admission_repertoire_id IS NULL\n            UNION SELECT rc.repertoire_id FROM repertoire_cards rc JOIN queue_attempt_origins o ON o.card_id=rc.card_id\n             WHERE o.queue_entry_id=%s AND o.card_id=%s AND o.revision=%s AND o.admission_repertoire_id IS NULL\n            ) r WHERE r.repertoire_id IS NOT NULL ORDER BY r.repertoire_id', (entry_id, card_id, expected_revision) * 3).fetchall()
