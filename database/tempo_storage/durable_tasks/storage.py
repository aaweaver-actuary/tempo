"""Existing fixed durable task SQL; caller owns scheduling, policy and transactions."""

from tempo_storage.controls.claim_sql import claimable, control_order


_EVENT_INSERT_SQL = 'INSERT INTO background_task_events(\n               task_id,generation,event,phase,detail,created_at\n           ) VALUES(%s,%s,%s,%s,%s,%s)'

_EVENT_PRUNE_SQL = 'DELETE FROM background_task_events\n           WHERE id IN (\n               SELECT id FROM background_task_events WHERE task_id=%s\n               ORDER BY id DESC LIMIT ALL OFFSET 100\n           )'

_TASK_BY_KIND_SQL = 'SELECT * FROM background_tasks WHERE kind=%s AND deduplication_key=%s'

_TASK_UPSERT_SQL = "INSERT INTO background_tasks(\n                   id,kind,deduplication_key,generation,priority,state,phase,\n                   payload_version,payload_json,attempt_count,max_attempts,\n                   next_attempt_at,lease_token,lease_expires_at,last_error,\n                   created_at,started_at,completed_at,updated_at\n               ) VALUES(%s,%s,%s,%s,%s,'queued','queued',1,%s,0,%s,%s,NULL,NULL,NULL,%s,NULL,NULL,%s)\n               ON CONFLICT(kind,deduplication_key) DO UPDATE SET\n                   generation=CASE WHEN background_tasks.generation>=excluded.generation THEN background_tasks.generation+1 ELSE excluded.generation END,\n                   priority=LEAST(background_tasks.priority,excluded.priority),\n                   state='queued',phase='queued',payload_version=excluded.payload_version,\n                   payload_json=excluded.payload_json,attempt_count=0,\n                   transaction_timeout_count=0,transaction_timeout_checkpoint=NULL,\n                   max_attempts=excluded.max_attempts,next_attempt_at=excluded.next_attempt_at,\n                   lease_token=NULL,lease_expires_at=NULL,last_error=NULL,\n                   completed_at=NULL,updated_at=excluded.updated_at"

_TASK_BY_ID_SQL = 'SELECT * FROM background_tasks WHERE id=%s'

_COMPLETE_SLICE_SQL = "UPDATE background_tasks SET state='complete',phase='published',\n               lease_token=NULL,lease_expires_at=NULL,last_error=NULL,\n               transaction_timeout_count=0,transaction_timeout_checkpoint=NULL,\n               completed_at=%s,updated_at=%s\n           WHERE id=%s AND generation=%s AND lease_token=%s AND state='leased'"

_QUEUE_REFRESH_STATUS_SQL = 'UPDATE queue_projections\n    SET state=%s,refresh_pending=%s,last_error=%s WHERE queue_date=%s'

_QUEUE_REFRESH_PROGRESS_SQL = 'UPDATE queue_projections SET last_error=NULL WHERE queue_date=%s'




def prepare_task_upsert():
    def write_task(database, values):
        return database.execute(
            _TASK_UPSERT_SQL + ' RETURNING *', values,
        ).fetchone()
    return write_task


def prepare_task_claim(kind, allowed_kinds, now, *, explicit_imports_only=False):
    kind_clause = ''
    kind_parameters = []
    if kind is not None:
        kind_clause = ' AND kind=%s'
        kind_parameters.append(kind)
    elif allowed_kinds is not None:
        kind_clause = ' AND kind IN (' + ','.join(('%s' for _ in allowed_kinds)) + ')'
        kind_parameters.extend(allowed_kinds)
    if explicit_imports_only:
        from tempo_storage.authored_repertoires.pgn_import.preparation import predicate
        kind_clause += ' AND ' + predicate()
    reclaim_parameters = (now, now, *kind_parameters, now)
    reclaim_statement = f"""UPDATE background_tasks
               SET state='queued',lease_token=NULL,lease_expires_at=NULL,
                   updated_at=%s
               WHERE id IN (SELECT id FROM background_tasks
                            WHERE state='leased' AND lease_expires_at<=%s{kind_clause}
                            ORDER BY lease_expires_at,id LIMIT 1 FOR UPDATE SKIP LOCKED)
               AND state='leased' AND lease_expires_at<=%s RETURNING id,kind,generation"""

    def reclaim(database):
        return database.execute(reclaim_statement, reclaim_parameters).fetchall()

    # Explicit import claims carry their own receipt scope; otherwise enforce Study mode.
    admission_clause = 'TRUE' if explicit_imports_only else "(kind='daily_queue' OR (SELECT study_only_mode FROM settings WHERE id=1)=0)"
    candidate_statement = f"""SELECT * FROM background_tasks
                    WHERE state IN ('queued','retrying') AND next_attempt_at<=%s{kind_clause}
                      AND {claimable('durable', 'background_tasks.id')}
                      AND {admission_clause}
                    ORDER BY priority,{control_order('durable', 'background_tasks.id')}next_attempt_at,created_at
                    LIMIT 1 FOR UPDATE OF background_tasks SKIP LOCKED"""
    def read_candidate(database, parameters):
        return database.execute(candidate_statement, parameters).fetchone()

    return reclaim, read_candidate, kind_parameters


def prepare_task_failure():
    def write_failure(database, values):
        return database.execute(
            """UPDATE background_tasks SET state=%s,next_attempt_at=%s,
                   lease_token=NULL,lease_expires_at=NULL,last_error=%s,
                   completed_at=%s,updated_at=%s WHERE id=%s AND generation=%s AND lease_token=%s AND state='leased'""",
            values,
        ).rowcount

    return write_failure


def prepare_contention_defer():
    def write_contention(database, values):
        return database.execute(
            """UPDATE background_tasks SET state='retrying',
               attempt_count=CASE WHEN attempt_count>0 THEN attempt_count-1 ELSE 0 END,
               next_attempt_at=%s,lease_token=NULL,lease_expires_at=NULL,
               last_error=NULL,updated_at=%s
               WHERE id=%s AND generation=%s AND lease_token=%s AND state='leased'""",
            values,
        ).rowcount

    return write_contention


def defer_for_connection_failure(database, values):
    return database.execute(
        "UPDATE background_tasks SET state='retrying',\n           attempt_count=CASE WHEN attempt_count>0 THEN attempt_count-1 ELSE 0 END,\n           next_attempt_at=%s,lease_token=NULL,lease_expires_at=NULL,\n           last_error='database_connection_unavailable',updated_at=%s\n           WHERE id=%s AND generation=%s AND lease_token=%s AND state='leased'",
        values,
    ).rowcount


def prepare_manual_retry():
    def write_retry(database, values):
        return database.execute(
            """UPDATE background_tasks SET state='queued',attempt_count=0,
                   transaction_timeout_count=0,transaction_timeout_checkpoint=NULL,
                   next_attempt_at=%s,lease_token=NULL,lease_expires_at=NULL,last_error=NULL,
                   completed_at=NULL,updated_at=%s WHERE id=%s""",
            values,
        )

    return write_retry


def prepare_interrupted_requeue(excluded_kinds=()):
    excluded_kinds = tuple(excluded_kinds)
    exclusion = (' AND kind NOT IN (' + ','.join('%s' for _ in excluded_kinds) + ')') if excluded_kinds else ''
    def write_interrupted(database, values):
        return database.execute(
            f"""UPDATE background_tasks SET state='queued',
                   lease_token=NULL,lease_expires_at=NULL,updated_at=%s WHERE state='leased'{exclusion}""",
            (*values, *excluded_kinds),
        )

    return write_interrupted


def insert_event(database, values):
    return database.execute(
        _EVENT_INSERT_SQL,
        values,
    )


def prune_events(database, values):
    return database.execute(
        _EVENT_PRUNE_SQL,
        values,
    )


def update_queue_status(database, values):
    return database.execute(
        _QUEUE_REFRESH_STATUS_SQL,
        values,
    )


def task_by_id(database, values):
    return database.execute(
        _TASK_BY_ID_SQL,
        values,
    ).fetchone()


def clear_queue_error(database, values):
    return database.execute(
        _QUEUE_REFRESH_PROGRESS_SQL,
        values,
    )


def task_by_kind(database, values):
    return database.execute(
        _TASK_BY_KIND_SQL,
        values,
    ).fetchone()


def enqueue_compact_followup(database, values):
    return database.execute_native(
        "WITH queued AS ("
        "INSERT INTO background_tasks("
        "id,kind,deduplication_key,generation,priority,state,phase,payload_version,"
        "payload_json,attempt_count,max_attempts,next_attempt_at,lease_token,"
        "lease_expires_at,last_error,created_at,started_at,completed_at,updated_at) "
        "VALUES(%s,%s,%s,1,%s,'queued','queued',1,%s,0,5,%s,"
        "NULL,NULL,NULL,%s,NULL,NULL,%s) "
        "ON CONFLICT(kind,deduplication_key) DO UPDATE SET "
        "generation=background_tasks.generation+1,"
        "priority=LEAST(background_tasks.priority,excluded.priority),"
        "state='queued',phase='queued',payload_version=1,payload_json=excluded.payload_json,"
        "attempt_count=0,max_attempts=5,next_attempt_at=excluded.next_attempt_at,"
        "transaction_timeout_count=0,transaction_timeout_checkpoint=NULL,"
        "lease_token=NULL,lease_expires_at=NULL,last_error=NULL,"
        "completed_at=NULL,updated_at=excluded.updated_at RETURNING id,generation) "
        "INSERT INTO background_task_events(task_id,generation,event,phase,detail,created_at) "
        "SELECT id,generation,'enqueued','queued',NULL,%s FROM queued",
        values,
    )


def claim_lease(database, values):
    return database.execute(
        "UPDATE background_tasks\n               SET state='leased',attempt_count=attempt_count+1,\n                   lease_token=%s,lease_expires_at=%s,started_at=COALESCE(started_at,%s),updated_at=%s\n               WHERE id=%s AND generation=%s AND state IN ('queued','retrying')",
        values,
    ).rowcount


def clear_pending_refresh(database, values):
    return database.execute(
        'UPDATE analysis_refresh_requests SET pending_since=NULL WHERE kind=%s AND repertoire_id=%s',
        values,
    )


def read_task(database, values):
    return database.execute(
        'SELECT * FROM background_tasks WHERE id=%s',
        values,
    ).fetchone()


def complete_task(database, values):
    return database.execute(
        "UPDATE background_tasks SET state='complete',phase='published',\n                   lease_token=NULL,lease_expires_at=NULL,last_error=NULL,\n                   transaction_timeout_count=0,transaction_timeout_checkpoint=NULL,\n                   completed_at=%s,updated_at=%s\n               WHERE id=%s AND generation=%s AND lease_token=%s AND state='leased'",
        values,
    ).rowcount


def lock_slice(database, values):
    return database.execute(
        'SELECT generation,lease_token,state FROM background_tasks WHERE id=%s FOR UPDATE',
        values,
    ).fetchone()


def advance_slice(database, values):
    return database.execute(
        "UPDATE background_tasks SET state='queued',phase=%s,payload_json=%s,\n               attempt_count=0,next_attempt_at=%s,lease_token=NULL,lease_expires_at=NULL,\n               last_error=NULL,updated_at=%s,\n               transaction_timeout_count=0,transaction_timeout_checkpoint=NULL\n           WHERE id=%s AND generation=%s AND lease_token=%s AND state='leased'",
        values,
    ).rowcount


def complete_slice(database, values):
    return database.execute(
        _COMPLETE_SLICE_SQL,
        values,
    ).rowcount


def failure_budget(database, values):
    return database.execute(
        "SELECT attempt_count,max_attempts,kind,payload_json FROM background_tasks WHERE id=%s AND generation=%s AND lease_token=%s AND state='leased'",
        values,
    ).fetchone()


def update_integrity_failure(database, values):
    return database.execute(
        'UPDATE repertoire_integrity_state SET scan_status=%s,scan_error=%s WHERE repertoire_id=%s AND scan_generation=%s',
        values,
    )


def defer_for_foreground(database, values):
    return database.execute(
        "UPDATE background_tasks SET state='retrying',\n               attempt_count=CASE WHEN attempt_count>0 THEN attempt_count-1 ELSE 0 END,\n               next_attempt_at=%s,lease_token=NULL,lease_expires_at=NULL,updated_at=%s\n               WHERE id=%s AND generation=%s AND lease_token=%s AND state='leased'",
        values,
    ).rowcount


def timeout_checkpoint(database, values):
    return database.execute(
        "SELECT phase,payload_json,transaction_timeout_count,transaction_timeout_checkpoint FROM background_tasks WHERE id=%s AND generation=%s AND lease_token=%s AND state='leased'",
        values,
    ).fetchone()


def defer_for_timeout(database, values):
    return database.execute(
        "UPDATE background_tasks SET state='retrying',\n               attempt_count=CASE WHEN attempt_count>0 THEN attempt_count-1 ELSE 0 END,\n               transaction_timeout_count=%s,transaction_timeout_checkpoint=%s,\n               next_attempt_at=%s,lease_token=NULL,lease_expires_at=NULL,\n               last_error=%s,updated_at=%s\n               WHERE id=%s AND generation=%s AND lease_token=%s AND state='leased'",
        values,
    ).rowcount


def failed_task(database, values):
    return database.execute(
        "SELECT * FROM background_tasks WHERE id=%s AND state='failed'",
        values,
    ).fetchone()


def reset_activity_progress(database, values):
    return database.execute(
        "UPDATE background_activity SET phase='Queued',completed_units=NULL,\n               total_units=NULL,updated_at=%s WHERE source='durable' AND work_id=%s",
        values,
    )


def interrupted_tasks(database, excluded_kinds=()):
    excluded_kinds = tuple(excluded_kinds)
    exclusion = (' AND kind NOT IN (' + ','.join('%s' for _ in excluded_kinds) + ')') if excluded_kinds else ''
    return database.execute(
        "SELECT id,generation,kind FROM background_tasks WHERE state='leased'" + exclusion,
        excluded_kinds,
    ).fetchall()


def visible_tasks(database):
    return database.execute(
        "SELECT * FROM background_tasks\n               WHERE state!='complete' OR updated_at::timestamptz>=(CURRENT_TIMESTAMP - INTERVAL '1 day')\n               ORDER BY CASE state WHEN 'failed' THEN 0 WHEN 'leased' THEN 1 ELSE 2 END,\n                        priority,created_at",
    ).fetchall()


def delivery_lease(database, values):
    return database.execute(
        'SELECT generation,lease_token,state FROM background_tasks WHERE id=%s',
        values,
    ).fetchone()


def task_exists(database, values):
    return database.execute(
        'SELECT 1 FROM background_tasks WHERE id=%s',
        values,
    ).fetchone()


def task_kind(database, values):
    return database.execute(
        'SELECT kind FROM background_tasks WHERE id=%s',
        values,
    ).fetchone()


def lock_or_create_current_queue_task(database, *, task_id, payload_json, priority,
                                      max_attempts, now, initial_generation=1,
                                      next_attempt_at=None, deduplication_key="current"):
    """Create or lock the singleton before taking any queue projection lock.

    The unique key serializes concurrent first requests.  The caller decides
    whether a pre-existing task can be coalesced and owns the transaction.
    """
    inserted = database.execute(
        "INSERT INTO background_tasks(\n               id,kind,deduplication_key,generation,priority,state,phase,\n               payload_version,payload_json,attempt_count,max_attempts,\n               next_attempt_at,lease_token,lease_expires_at,last_error,\n               created_at,started_at,completed_at,updated_at)\n           VALUES(%s,'daily_queue',%s,%s,%s,'queued','queued',1,%s,0,%s,%s,\n                  NULL,NULL,NULL,%s,NULL,NULL,%s)\n           ON CONFLICT(kind,deduplication_key) DO NOTHING RETURNING *",
        (task_id, deduplication_key, initial_generation, priority, payload_json, max_attempts,
         next_attempt_at or now, now, now),
    ).fetchone()
    if inserted is not None:
        return inserted, True
    statement = _TASK_BY_KIND_SQL + " FOR UPDATE"
    task = database.execute(statement, ("daily_queue", deduplication_key)).fetchone()
    if task is None:
        raise RuntimeError("Current queue task disappeared while acquiring its lock")
    return task, False


def locked_queue_task_for_day(database, queue_date):
    """Inspect an existing alternate while holding the singleton lock."""
    return database.execute(
        _TASK_BY_KIND_SQL + " FOR UPDATE", ("daily_queue", "current-day:" + queue_date)
    ).fetchone()


def update_locked_queue_task_payload(database, *, task_id, generation, payload_json):
    """Replace a caller-merged payload while preserving active task state.

    Caller must hold the task row lock and validate its day and active state.
    """
    return database.execute(
        "UPDATE background_tasks SET payload_json=%s\n           WHERE id=%s AND generation=%s AND kind='daily_queue'\n             AND state IN ('queued','leased','retrying')",
        (payload_json, task_id, generation),
    ).rowcount == 1
