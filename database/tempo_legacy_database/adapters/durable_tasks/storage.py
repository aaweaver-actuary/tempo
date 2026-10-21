"""Existing fixed durable task SQL; caller owns scheduling, policy and transactions."""

from .. import postgres_store
from ..controls.claim_sql import claimable, control_order
from ..defensive_analysis import task_admission_sql


_EVENT_INSERT_SQL = """INSERT INTO background_task_events(
               task_id,generation,event,phase,detail,created_at
           ) VALUES(?,?,?,?,?,?)"""

_EVENT_PRUNE_SQL = """DELETE FROM background_task_events
           WHERE id IN (
               SELECT id FROM background_task_events WHERE task_id=?
               ORDER BY id DESC LIMIT -1 OFFSET 100
           )"""

_TASK_BY_KIND_SQL = "SELECT * FROM background_tasks WHERE kind=? AND deduplication_key=?"

_TASK_UPSERT_SQL = """INSERT INTO background_tasks(
                   id,kind,deduplication_key,generation,priority,state,phase,
                   payload_version,payload_json,attempt_count,max_attempts,
                   next_attempt_at,lease_token,lease_expires_at,last_error,
                   created_at,started_at,completed_at,updated_at
               ) VALUES(?,?,?,?,?,'queued','queued',1,?,0,?,?,NULL,NULL,NULL,?,NULL,NULL,?)
               ON CONFLICT(kind,deduplication_key) DO UPDATE SET
                   generation=CASE WHEN background_tasks.generation>=excluded.generation THEN background_tasks.generation+1 ELSE excluded.generation END,
                   priority=MIN(background_tasks.priority,excluded.priority),
                   state='queued',phase='queued',payload_version=excluded.payload_version,
                   payload_json=excluded.payload_json,attempt_count=0,
                   transaction_timeout_count=0,transaction_timeout_checkpoint=NULL,
                   max_attempts=excluded.max_attempts,next_attempt_at=excluded.next_attempt_at,
                   lease_token=NULL,lease_expires_at=NULL,last_error=NULL,
                   completed_at=NULL,updated_at=excluded.updated_at"""

_TASK_BY_ID_SQL = "SELECT * FROM background_tasks WHERE id=?"

_SELECTED_REBUILD_UPSERT_FENCE = (
    " WHERE background_tasks.id=? AND background_tasks.generation=? AND background_tasks.state=? "
    "AND background_tasks.phase=? AND background_tasks.lease_token IS NULL AND background_tasks.lease_expires_at IS NULL"
)

_COMPLETE_SLICE_SQL = """UPDATE background_tasks SET state='complete',phase='published',
               lease_token=NULL,lease_expires_at=NULL,last_error=NULL,
               transaction_timeout_count=0,transaction_timeout_checkpoint=NULL,
               completed_at=?,updated_at=?
           WHERE id=? AND generation=? AND lease_token=? AND state='leased'"""

_QUEUE_REFRESH_STATUS_SQL = """UPDATE queue_projections
    SET state=?,refresh_pending=?,last_error=? WHERE queue_date=?"""

_QUEUE_REFRESH_PROGRESS_SQL = "UPDATE queue_projections SET last_error=NULL WHERE queue_date=?"


def warm_completion_sql() -> None:
    """Translate durable-task completion SQL before a 50 ms PostgreSQL section."""

    from tempo_legacy_database.adapters.postgres_store import postgres_sql

    for statement in (
        _TASK_BY_KIND_SQL, _TASK_UPSERT_SQL, _TASK_BY_ID_SQL,
        _TASK_UPSERT_SQL + _SELECTED_REBUILD_UPSERT_FENCE, _TASK_UPSERT_SQL + ' WHERE FALSE',
        _EVENT_INSERT_SQL, _EVENT_PRUNE_SQL, _COMPLETE_SLICE_SQL,
        _QUEUE_REFRESH_STATUS_SQL, _QUEUE_REFRESH_PROGRESS_SQL,
    ):
        postgres_sql(statement)


def configured() -> bool:
    return postgres_store.configured()


def supports_native_execution(database) -> bool:
    return hasattr(database, 'execute_native')


def failure_state_parameters(state):
    return (state,) if not postgres_store.configured() else ()


def prepare_task_upsert(prior=None, *, selected=False):
    fence, fence_parameters = ('', ())
    if selected:
        fence = _SELECTED_REBUILD_UPSERT_FENCE if prior else ' WHERE FALSE'
        fence_parameters = tuple((prior[field] for field in ('id', 'generation', 'state', 'phase'))) if prior else ()

    def write_task(database, values):
        return database.execute(
            _TASK_UPSERT_SQL + fence + " RETURNING *",
            (*values, *fence_parameters),
        ).fetchone()

    return write_task


def prepare_task_claim(kind, allowed_kinds, now, defensive_kinds):
    kind_clause = ''
    kind_parameters = []
    if kind is not None:
        kind_clause = ' AND kind=?'
        kind_parameters.append(kind)
    elif allowed_kinds is not None:
        kind_clause = ' AND kind IN (' + ','.join(('?' for _ in allowed_kinds)) + ')'
        kind_parameters.extend(allowed_kinds)
    reclaim_parameters = (now, now, *kind_parameters, now)
    reclaim_statement = f"""UPDATE background_tasks
               SET state='queued',lease_token=NULL,lease_expires_at=NULL,
                   updated_at=?
               WHERE id IN (SELECT id FROM background_tasks
                            WHERE state='leased' AND lease_expires_at<=?{kind_clause}
                            ORDER BY lease_expires_at,id LIMIT 1)
               AND state='leased' AND lease_expires_at<=? RETURNING id,kind,generation"""

    def reclaim(database):
        if hasattr(database, 'execute_native'):
            reclaimed = database.execute_native(postgres_store.postgres_sql(reclaim_statement).replace('LIMIT 1)', 'LIMIT 1 FOR UPDATE SKIP LOCKED)'), reclaim_parameters).fetchall()
        else:
            reclaimed = database.execute(reclaim_statement, reclaim_parameters).fetchall()
        return reclaimed

    def read_candidate(database, parameters):
        return database.execute(
            f"""SELECT * FROM background_tasks
                    WHERE state IN ('queued','retrying') AND next_attempt_at<=?{kind_clause}
                      AND {claimable('durable', 'background_tasks.id')}
                      AND {task_admission_sql('background_tasks.kind', defensive_kinds)}
                    ORDER BY priority,{control_order('durable', 'background_tasks.id')}next_attempt_at,created_at LIMIT 1""",
            parameters,
        ).fetchone()

    return reclaim, read_candidate, kind_parameters


def prepare_task_failure():
    retry_phase = 'phase' if postgres_store.configured() else '?'

    def write_failure(database, values):
        return database.execute(
            f"""UPDATE background_tasks SET state=?,phase={retry_phase},next_attempt_at=?,
                   lease_token=NULL,lease_expires_at=NULL,last_error=?,
                   completed_at=?,updated_at=? WHERE id=? AND generation=? AND lease_token=? AND state='leased'""",
            values,
        ).rowcount

    return write_failure


def prepare_contention_defer():
    yielded_phase = '' if postgres_store.configured() else ",phase='yielded'"

    def write_contention(database, values):
        return database.execute(
            f"""UPDATE background_tasks SET state='retrying'{yielded_phase},
               attempt_count=CASE WHEN attempt_count>0 THEN attempt_count-1 ELSE 0 END,
               next_attempt_at=?,lease_token=NULL,lease_expires_at=NULL,
               last_error=NULL,updated_at=?
               WHERE id=? AND generation=? AND lease_token=? AND state='leased'""",
            values,
        ).rowcount

    return write_contention


def prepare_manual_retry():
    manual_phase = '' if postgres_store.configured() else ",phase='queued'"

    def write_retry(database, values):
        return database.execute(
            f"""UPDATE background_tasks SET state='queued'{manual_phase},attempt_count=0,
                   transaction_timeout_count=0,transaction_timeout_checkpoint=NULL,
                   next_attempt_at=?,lease_token=NULL,lease_expires_at=NULL,last_error=NULL,
                   completed_at=NULL,updated_at=? WHERE id=?""",
            values,
        )

    return write_retry


def prepare_interrupted_requeue():
    interrupted_phase = '' if postgres_store.configured() else ",phase='reclaimed'"

    def write_interrupted(database, values):
        return database.execute(
            f"""UPDATE background_tasks SET state='queued'{interrupted_phase},
                   lease_token=NULL,lease_expires_at=NULL,updated_at=? WHERE state='leased'""",
            values,
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
        """UPDATE background_tasks
               SET state='leased',attempt_count=attempt_count+1,
                   lease_token=?,lease_expires_at=?,started_at=COALESCE(started_at,?),updated_at=?
               WHERE id=? AND generation=? AND state IN ('queued','retrying')""",
        values,
    ).rowcount


def clear_pending_refresh(database, values):
    return database.execute(
        'UPDATE analysis_refresh_requests SET pending_since=NULL WHERE kind=? AND repertoire_id=?',
        values,
    )


def read_task(database, values):
    return database.execute(
        "SELECT * FROM background_tasks WHERE id=?",
        values,
    ).fetchone()


def complete_task(database, values):
    return database.execute(
        """UPDATE background_tasks SET state='complete',phase='published',
                   lease_token=NULL,lease_expires_at=NULL,last_error=NULL,
                   transaction_timeout_count=0,transaction_timeout_checkpoint=NULL,
                   completed_at=?,updated_at=?
               WHERE id=? AND generation=? AND lease_token=? AND state='leased'""",
        values,
    ).rowcount


def lock_slice(database, values):
    return database.execute(
        "SELECT generation,lease_token,state FROM background_tasks WHERE id=? FOR UPDATE",
        values,
    ).fetchone()


def advance_slice(database, values):
    return database.execute(
        """UPDATE background_tasks SET state='queued',phase=?,payload_json=?,
               attempt_count=0,next_attempt_at=?,lease_token=NULL,lease_expires_at=NULL,
               last_error=NULL,updated_at=?,
               transaction_timeout_count=0,transaction_timeout_checkpoint=NULL
           WHERE id=? AND generation=? AND lease_token=? AND state='leased'""",
        values,
    ).rowcount


def complete_slice(database, values):
    return database.execute(
        _COMPLETE_SLICE_SQL,
        values,
    ).rowcount


def failure_budget(database, values):
    return database.execute(
        "SELECT attempt_count,max_attempts,kind,payload_json FROM background_tasks WHERE id=? AND generation=? AND lease_token=? AND state='leased'",
        values,
    ).fetchone()


def fail_sync_job(database, values):
    return database.execute(
        """UPDATE game_sync_jobs SET status='failed',error=?,completed_at=?,updated_at=?
                   WHERE id=? AND status IN ('queued','running','paused','retrying')""",
        values,
    )


def sync_window_provider(database, values):
    return database.execute(
        "SELECT provider FROM game_sync_windows WHERE id=?",
        values,
    ).fetchone()


def fail_sync_provider(database, values):
    return database.execute(
        "UPDATE game_sync_state SET status='error',last_error=? WHERE provider=?",
        values,
    )


def update_integrity_failure(database, values):
    return database.execute(
        "UPDATE repertoire_integrity_state SET scan_status=?,scan_error=? "
                "WHERE repertoire_id=? AND scan_generation=?",
        values,
    )


def fail_coverage_run(database, values):
    return database.execute(
        "UPDATE repertoire_coverage_runs SET status='failed',last_error=?,updated_at=? "
                "WHERE id=? AND status IN ('building','queued','running')",
        values,
    )


def fail_analysis_publication(database, values):
    return database.execute(
        "UPDATE game_analysis_publications SET status='failed',last_error=?,updated_at=? "
                "WHERE game_id=? AND status IN ('queued','publishing')",
        values,
    )


def fail_analysis_job(database, values):
    return database.execute(
        "UPDATE game_analysis_jobs SET status='failed',last_error=?,updated_at=? "
                "WHERE game_id=? AND status='publishing'",
        values,
    )


def fail_imported_game(database, values):
    return database.execute(
        "UPDATE imported_games SET analysis_state='failed' WHERE id=?",
        values,
    )


def defer_for_foreground(database, values):
    return database.execute(
        """UPDATE background_tasks SET state='retrying',
               attempt_count=CASE WHEN attempt_count>0 THEN attempt_count-1 ELSE 0 END,
               next_attempt_at=?,lease_token=NULL,lease_expires_at=NULL,updated_at=?
               WHERE id=? AND generation=? AND lease_token=? AND state='leased'""",
        values,
    ).rowcount


def timeout_checkpoint(database, values):
    return database.execute(
        "SELECT phase,payload_json,transaction_timeout_count,transaction_timeout_checkpoint "
            "FROM background_tasks WHERE id=? AND generation=? AND lease_token=? AND state='leased'",
        values,
    ).fetchone()


def defer_for_timeout(database, values):
    return database.execute(
        """UPDATE background_tasks SET state='retrying',
               attempt_count=CASE WHEN attempt_count>0 THEN attempt_count-1 ELSE 0 END,
               transaction_timeout_count=?,transaction_timeout_checkpoint=?,
               next_attempt_at=?,lease_token=NULL,lease_expires_at=NULL,
               last_error=?,updated_at=?
               WHERE id=? AND generation=? AND lease_token=? AND state='leased'""",
        values,
    ).rowcount


def failed_task(database, values):
    return database.execute(
        "SELECT * FROM background_tasks WHERE id=? AND state='failed'",
        values,
    ).fetchone()


def reset_activity_progress(database, values):
    return database.execute(
        """UPDATE background_activity SET phase='Queued',completed_units=NULL,
               total_units=NULL,updated_at=? WHERE source='durable' AND work_id=?""",
        values,
    )


def interrupted_tasks(database):
    return database.execute(
        "SELECT id,generation,kind FROM background_tasks WHERE state='leased'",
    ).fetchall()


def visible_tasks(database):
    return database.execute(
        """SELECT * FROM background_tasks
               WHERE state!='complete' OR updated_at>=datetime('now','-1 day')
               ORDER BY CASE state WHEN 'failed' THEN 0 WHEN 'leased' THEN 1 ELSE 2 END,
                        priority,created_at""",
    ).fetchall()


def defer_disabled_defensive_task(database, values):
    return database.execute(
        """UPDATE background_tasks SET state='queued',phase='queued',lease_token=NULL,
               lease_expires_at=NULL,attempt_count=MAX(0,attempt_count-1),updated_at=?
               WHERE id=? AND generation=? AND lease_token=? AND state='leased'""",
        values,
    )


def delivery_lease(database, values):
    return database.execute(
        "SELECT generation,lease_token,state FROM background_tasks WHERE id=?",
        values,
    ).fetchone()


def task_exists(database, values):
    return database.execute(
        "SELECT 1 FROM background_tasks WHERE id=?",
        values,
    ).fetchone()


def task_kind(database, values):
    return database.execute(
        "SELECT kind FROM background_tasks WHERE id=?",
        values,
    ).fetchone()
