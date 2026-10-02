"""Issue #37: observable progress is distinct from queue and delivery activity."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import threading
import time

import pytest
from pydantic import ValidationError

from app import database
from app.services import durable_tasks, background_diagnostics, background_runtime
from app.services.background_metrics import increment, BackgroundDiagnostics
from app.services.engine_diagnostics import record_engine_outcome
from app.services.activity_gate import ApplicationActivityGate


@pytest.fixture
def diagnostic_database(tmp_path, monkeypatch):
    monkeypatch.delenv('TEMPO_DATABASE_WRITE_URL', raising=False)
    monkeypatch.delenv('TEMPO_DATABASE_READ_URL', raising=False)
    monkeypatch.delenv('TEMPO_REDIS_URL', raising=False)
    monkeypatch.delenv('TEMPO_FOREGROUND_ACTIVITY_URL', raising=False)
    monkeypatch.setattr(database, 'DB_PATH', tmp_path/'observability.db')
    database.initialize()
    with database.connection() as connection:
        connection.execute('DELETE FROM background_tasks')
    def submit(operation, **kwargs):
        with database.connection() as connection:
            return operation(connection)
    monkeypatch.setattr(durable_tasks,'submit_foreground_write',submit)
    def submit_background(operation, **kwargs):
        with database.background_connection() as connection:
            return operation(connection)
    monkeypatch.setattr(durable_tasks,'submit_background_write',submit_background)
    return database.DB_PATH


def task(key='one', **kwargs):
    return durable_tasks.enqueue_task('daily_queue',key,{},**kwargs)


def counts(kind='daily_queue'):
    snapshot=background_diagnostics.snapshot()
    assert snapshot.available, snapshot
    matches=[value for value in snapshot.counters if value.kind==kind]
    return matches[0].counts if matches else None


def test_background_known_kind_lifecycle_has_no_redundant_kind_select(diagnostic_database, monkeypatch):
    statements = []
    original_connection = database.connection

    @contextmanager
    def traced_connection(**options):
        with original_connection(**options) as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(database, 'connection', traced_connection)
    task(max_attempts=1)
    task(max_attempts=1)  # replacement event
    claimed = durable_tasks.claim_task('daily_queue')
    durable_tasks.fail_task(claimed['id'], claimed['generation'], claimed['lease_token'], RuntimeError('synthetic'))
    durable_tasks.retry_task(claimed['id'])
    claimed = durable_tasks.claim_task('daily_queue')
    with database.background_connection() as connection:
        assert durable_tasks.advance_task_slice_in_transaction(connection, claimed, next_phase='synthetic', next_payload={})
    durable_tasks.claim_task('daily_queue')
    durable_tasks.requeue_interrupted_tasks()
    claimed = durable_tasks.claim_task('daily_queue')
    with database.background_connection() as connection:
        assert durable_tasks.complete_task_slice_in_transaction(connection, claimed)
    task('complete')
    claimed = durable_tasks.claim_task('daily_queue')
    assert durable_tasks.complete_task(claimed['id'], claimed['generation'], claimed['lease_token'], kind=claimed['kind'])
    task('defer')
    claimed = durable_tasks.claim_task('daily_queue')
    assert durable_tasks.defer_task_for_contention(claimed['id'], claimed['generation'], claimed['lease_token'], kind=claimed['kind'])
    assert counts().completed_generations == 2
    assert counts().slices == 1
    assert counts().generation_restarts == 2  # manual retry and interrupted lease
    assert counts().contention_deferrals == 1
    redundant = [statement for statement in statements
                 if statement.strip().lower().startswith('select kind from background_tasks where id=')]
    assert redundant == []


def test_background_id_only_event_keeps_single_kind_lookup(diagnostic_database):
    row = task()
    statements = []
    with database.background_connection() as connection:
        connection.set_trace_callback(statements.append)
        durable_tasks._record_event(connection, row['id'], row['generation'], 'slice_complete')
    assert counts().slices == 1
    assert sum(statement.lower().startswith('select kind from background_tasks where id=')
               for statement in statements) == 1


def test_background_diagnostics_classifies_queue_states_and_eligibility(diagnostic_database):
    states=['queued','leased','retrying','failed','complete']
    for state in states:
        row=task(state)
        with database.connection() as connection:
            connection.execute('UPDATE background_tasks SET state=? WHERE id=?',(state,row['id']))
    delayed=task('delayed',delay_seconds=3600)
    paused=task('paused')
    with database.connection() as connection:
        connection.execute("INSERT INTO background_activity(source,work_id,paused,updated_at) VALUES('durable',?,1,?)",(paused['id'],paused['updated_at']))
    projection=background_diagnostics.snapshot()
    by_state={value.state:value for value in projection.queues if value.queue=='durable'}
    assert set(by_state)=={'queued','delayed','paused','leased','retrying','failed','complete'}
    assert all(value.count==1 for value in by_state.values())
    assert by_state['delayed'].next_eligibility_seconds>3500
    assert by_state['complete'].oldest_pending_age_seconds is None
    assert by_state['retrying'].underlying_state=='retrying'


def test_background_pending_age_survives_retry_deferral_and_reclaim(diagnostic_database):
    origin=datetime.now(timezone.utc)-timedelta(hours=2)
    row=task()
    with database.connection() as connection:
        connection.execute('UPDATE background_tasks SET pending_since=? WHERE id=?',(origin.isoformat(),row['id']))
    claimed=durable_tasks.claim_task('daily_queue')
    durable_tasks.defer_task_for_contention(claimed['id'],claimed['generation'],claimed['lease_token'])
    with database.connection() as connection:
        connection.execute('UPDATE background_tasks SET next_attempt_at=? WHERE id=?',(origin.isoformat(),row['id']))
    claimed=durable_tasks.claim_task('daily_queue')
    durable_tasks.fail_task(claimed['id'],claimed['generation'],claimed['lease_token'],RuntimeError('synthetic'))
    with database.connection() as connection:
        connection.execute('UPDATE background_tasks SET next_attempt_at=? WHERE id=?',(origin.isoformat(),row['id']))
    claimed=durable_tasks.claim_task('daily_queue')
    with database.connection() as connection:
        connection.execute('UPDATE background_tasks SET lease_expires_at=? WHERE id=?',(origin.isoformat(),row['id']))
    durable_tasks.claim_task('daily_queue')
    with database.read_connection() as connection:
        assert connection.execute('SELECT pending_since FROM background_tasks WHERE id=?',(row['id'],)).fetchone()[0]==origin.isoformat()
    assert counts().contention_deferrals==1
    assert counts().retries==1
    assert counts().lease_reclaims==1


def test_background_generation_replacement_and_restart_are_visible(diagnostic_database):
    original=task()
    replacement=task()
    assert replacement['pending_since']==original['pending_since']
    assert replacement['generation_started_at']>=original['generation_started_at']
    claimed=durable_tasks.claim_task('daily_queue')
    durable_tasks.requeue_interrupted_tasks()
    assert counts().generation_replacements==1
    assert counts().generation_restarts==1
    current=durable_tasks.claim_task('daily_queue')
    assert not durable_tasks.complete_task(claimed['id'],claimed['generation'],claimed['lease_token'])
    assert durable_tasks.complete_task(current['id'],current['generation'],current['lease_token'])
    new_period=task()
    assert new_period['pending_since']>original['pending_since']
    assert counts().generation_replacements==1


def test_background_duplicate_delivery_does_not_inflate_semantic_completion(diagnostic_database):
    task()
    claimed=durable_tasks.claim_task('daily_queue')
    assert durable_tasks.complete_task(claimed['id'],claimed['generation'],claimed['lease_token'])
    assert not durable_tasks.complete_task(claimed['id'],claimed['generation'],claimed['lease_token'])
    assert counts().completed_generations==1
    assert counts().useful_completions==0  # generic terminal task is not proof of an analysis
    assert counts().stale_results==1


def test_background_admission_wait_is_separate_from_handler_execution(monkeypatch):
    clock=[0.0]
    monkeypatch.setattr(background_runtime.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(background_runtime.redis_admission_gate,'configured',lambda:False)
    with background_runtime.measure_handler('daily_queue') as measurement:
        clock[0]=2
        with background_runtime.admission_wait():
            clock[0]=5
            with background_runtime.admission_wait():
                clock[0]=8
        clock[0]=10
        result=measurement.sample()
    assert result.admission_wait_seconds==6
    assert result.handler_elapsed_seconds==10
    assert result.execution_seconds==4


def test_background_stale_delivery_and_result_discard_are_distinct(diagnostic_database):
    task()
    claimed=durable_tasks.claim_task('daily_queue')
    task()
    assert not durable_tasks.current_delivery(claimed)
    durable_tasks.record_stale_delivery(claimed)
    with database.connection() as connection:
        assert not durable_tasks.complete_task_slice_in_transaction(connection,claimed)
    assert counts().stale_deliveries==1
    assert counts().stale_results==1
    assert counts().completed_generations==0


def test_background_lease_expiry_and_reclaim_are_counted_once(diagnostic_database):
    task()
    claimed=durable_tasks.claim_task('daily_queue')
    with database.connection() as connection:
        connection.execute('UPDATE background_tasks SET lease_expires_at=? WHERE id=?',('2000-01-01T00:00:00+00:00',claimed['id']))
    durable_tasks.claim_task('daily_queue')
    assert durable_tasks.claim_task('daily_queue') is None
    assert counts().lease_expiries==1
    assert counts().lease_reclaims==1


def test_engine_preemption_seconds_are_separate_from_successful_work(diagnostic_database):
    with database.connection() as connection:
        record_engine_outcome(connection,'engine_game','one',{'diagnostics':{'outcome':'preempted','elapsed_seconds':3.5}})
        record_engine_outcome(connection,'engine_game','two',{'diagnostics':{'outcome':'success','elapsed_seconds':2}},completed=True)
        record_engine_outcome(connection,'engine_game','three',{'diagnostics':{'outcome':'timeout','elapsed_seconds':55}})
        record_engine_outcome(connection,'engine_game','four',{'error':'synthetic failure'})
    result=counts('engine_game')
    assert result.engine_preemptions==1
    assert result.engine_preempted_seconds==3.5
    assert result.engine_abandoned_seconds==58.5
    assert result.engine_successful_seconds==2
    assert result.engine_completed_positions==result.useful_completions==1
    assert result.engine_timeouts==result.engine_failures==1


def test_background_snapshot_cost_is_independent_of_event_history(diagnostic_database,monkeypatch):
    row=task()
    with database.connection() as connection:
        connection.executemany('INSERT INTO background_task_events(task_id,generation,event,created_at) VALUES(?,1,?,?)',
            [(row['id'],'synthetic',row['created_at'])]*100_000)
    queries=[]
    original=background_diagnostics.read_connection
    @contextmanager
    def traced():
        with original() as connection:
            connection.set_trace_callback(queries.append)
            yield connection
    monkeypatch.setattr(background_diagnostics,'read_connection',traced)
    result=background_diagnostics.snapshot()
    assert result.available
    assert result.query_duration_seconds<background_diagnostics.QUERY_BUDGET_SECONDS
    assert not any('background_task_events' in statement for statement in queries)
    with database.connection() as connection:
        durable_tasks._record_event(connection,row['id'],1,'slice_complete')
        assert connection.execute('SELECT COUNT(*) FROM background_task_events').fetchone()[0]==100


def test_background_diagnostics_redaction_and_schema_parity(diagnostic_database):
    row=task()
    with database.connection() as connection:
        connection.execute('UPDATE background_tasks SET payload_json=?,last_error=?,deduplication_key=? WHERE id=?',
            (json.dumps({'token':'canary-secret'}),'canary-secret','canary-secret',row['id']))
    result=background_diagnostics.snapshot()
    assert 'canary-secret' not in result.model_dump_json()
    assert BackgroundDiagnostics.model_validate_json(result.model_dump_json())==result
    with pytest.raises(ValidationError):
        BackgroundDiagnostics.model_validate({**result.model_dump(),'payload':'canary-secret'})


def test_background_metric_buckets_expire_without_unbounded_growth(diagnostic_database):
    start=datetime(2026,10,2,tzinfo=timezone.utc)
    with database.connection() as connection:
        for bucket in range(600):
            increment(connection,'daily_queue','fixed',now=start+timedelta(seconds=bucket*300),claims=1)
        rows=connection.execute('SELECT * FROM background_metric_buckets').fetchall()
    assert len(rows)==288
    assert all(row['claims']==1 for row in rows)


def test_background_diagnostics_preserves_foreground_responsiveness(monkeypatch):
    monkeypatch.delenv('TEMPO_REDIS_URL',raising=False)
    gate=ApplicationActivityGate()
    entered=threading.Event()
    finished=threading.Event()
    samples=[]
    def background():
        with background_runtime.measure_handler('daily_queue') as measurement:
            entered.set()
            with gate.background_database_section():
                samples.append(measurement.sample())
        finished.set()
    with gate.foreground():
        worker=threading.Thread(target=background)
        worker.start()
        assert entered.wait(1)
        assert not finished.wait(0.02)
        assert gate.foreground_requests_active
    worker.join(1)
    assert finished.is_set()
    assert samples[0].admission_wait_seconds>0


def test_background_diagnostics_query_deadline_returns_unavailable(diagnostic_database,monkeypatch):
    monkeypatch.setattr(background_diagnostics,'QUERY_BUDGET_SECONDS',0)
    result=background_diagnostics.snapshot()
    assert not result.available
    assert result.unavailable_reason=='query_deadline'
    assert result.queues==result.counters==[]


def test_engine_callback_replay_does_not_inflate_position_or_preemption_counts(diagnostic_database):
    from app import game_analysis_commands
    now=datetime.now(timezone.utc).isoformat()
    class NativeSqlite:
        def __init__(self,connection):
            self.connection=connection
        def execute_native(self,statement,parameters=()):
            return self.connection.execute(statement.replace('%s','?').replace('FOR UPDATE',''),parameters)
        def execute(self,statement,parameters=()):
            return self.connection.execute(statement.replace('FOR UPDATE',''),parameters)
    with database.connection() as connection:
        connection.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES('synthetic','lichess','private-canary',?,'rapid',1,'white','1-0','synthetic','[]')",(now,))
        connection.execute("INSERT INTO game_analysis_jobs(game_id,status,lease_id,updated_at) VALUES('synthetic','leased','parent',?)",(now,))
        connection.execute("INSERT INTO game_analysis_position_reports(id,game_id,analysis_version,scan_pass,position_index,request_json,state,lease_id,parent_lease_id,updated_at) VALUES('position','synthetic',1,'shallow',0,'{}','leased','lease','parent',?)",(now,))
        adapter=NativeSqlite(connection)
        payload={'report_id':'position','lease_id':'lease','report':{},'request_json':'{}','diagnostics':{'outcome':'success','elapsed_seconds':2}}
        assert game_analysis_commands.publish_position_report(adapter,payload)=={'status':'complete'}
        assert game_analysis_commands.publish_position_report(adapter,payload)=={'status':'complete'}
        connection.execute("UPDATE game_analysis_position_reports SET state='leased',lease_id='new',parent_lease_id='parent' WHERE id='position'")
        release={'report_id':'position','lease_id':'new','diagnostics':{'outcome':'preempted','elapsed_seconds':3}}
        assert game_analysis_commands.release_position_report(adapter,release)=={'status':'queued'}
        assert game_analysis_commands.release_position_report(adapter,release)=={'status':'stale'}
    result=counts('engine_game')
    assert result.engine_completed_positions==1
    assert result.engine_successful_seconds==2
    assert result.engine_preemptions==1
    assert result.engine_preempted_seconds==3
    assert 'private-canary' not in background_diagnostics.snapshot().model_dump_json()


def test_background_oldest_eligible_age_excludes_delayed_paused_and_blocked(diagnostic_database):
    task('eligible')
    delayed=task('delayed',delay_seconds=3600)
    paused=task('paused')
    blocked=durable_tasks.enqueue_task('unsupported','blocked',{})
    old=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
    with database.connection() as connection:
        connection.execute('UPDATE background_tasks SET pending_since=? WHERE id IN (?,?,?)',(old,delayed['id'],paused['id'],blocked['id']))
        connection.execute("INSERT INTO background_activity(source,work_id,paused,updated_at) VALUES('durable',?,1,?)",(paused['id'],old))
    result=background_diagnostics.snapshot()
    states={entry.state:entry for entry in result.queues if entry.queue=='durable'}
    assert states['queued'].oldest_pending_age_seconds<10
    assert states['delayed'].oldest_pending_age_seconds>=86399
    assert states['paused'].oldest_pending_age_seconds>=86399
    assert states['blocked'].oldest_pending_age_seconds>=86399


def test_background_public_diagnostics_reject_invalid_counters_and_old_engine_fields(diagnostic_database):
    from app.models import ThreatAnalysisSubmission, EngineAttemptDiagnostics
    from app.services.background_metrics import MetricCounts
    assert ThreatAnalysisSubmission(lease_id='old',report={}).diagnostics is None
    for fields in ({'engine_preemptions':-1},{'engine_preemptions':'1'},{'engine_abandoned_seconds':float('inf')},{'payload':{}}):
        with pytest.raises(ValidationError):
            MetricCounts.model_validate(fields)
    for fields in ({'outcome':'success','elapsed_seconds':float('inf')},{'outcome':'private-canary','elapsed_seconds':1}):
        with pytest.raises(ValidationError):
            EngineAttemptDiagnostics.model_validate(fields)


def test_background_metric_outcomes_roll_back_with_their_transaction(diagnostic_database):
    with pytest.raises(RuntimeError):
        with database.connection() as connection:
            increment(connection,'engine_game','rollback',engine_completed_positions=2)
            raise RuntimeError('synthetic rollback')
    assert counts('engine_game') is None


def test_postgres_background_counter_flush_follows_domain_writes_and_sorts_locks():
    from app.postgres_store import PostgresConnection
    statements=[]
    class Raw:
        def execute(self,statement,parameters=()):
            statements.append((statement,parameters))
        def commit(self):
            statements.append(('COMMIT',()))
        def rollback(self):
            statements.append(('ROLLBACK',()))
    connection=PostgresConnection(Raw())
    increment(connection,'engine_game','same',claims=1)
    increment(connection,'daily_queue','other',claims=1)
    increment(connection,'engine_game','same',claims=1)
    connection.execute_native('domain publication')
    assert [statement for statement,_ in statements]==['domain publication']
    connection.commit()
    assert [parameters[0] for statement,parameters in statements if statement.startswith('INSERT')]==['daily_queue','engine_game']
    assert statements[-2][1][-1]==2
    assert statements[-1][0]=='COMMIT'
    statements.clear()
    increment(connection,'engine_game','same',claims=1)
    connection.rollback()
    connection.commit()
    assert not any(statement.startswith('INSERT') for statement,_ in statements)


def test_background_stale_delivery_skips_the_expensive_handler(diagnostic_database,monkeypatch):
    from app import tasks
    task()
    claimed=durable_tasks.claim_task('daily_queue')
    task()
    executions=[]
    monkeypatch.setattr(tasks,'execute_postgres_queue_refresh_slice',lambda _task: executions.append(True))
    assert tasks.execute_background_slice.run(claimed) is False
    assert executions==[]
    assert counts().stale_deliveries==1
    assert counts().completed_generations==0


def test_background_missing_task_delivery_is_observed_without_a_dangling_event(diagnostic_database):
    task()
    claimed=durable_tasks.claim_task('daily_queue')
    with database.connection() as connection:
        connection.execute('DELETE FROM background_tasks')
    durable_tasks.record_stale_delivery(claimed)
    assert counts().stale_deliveries==1


def test_background_stale_publication_lock_and_removed_result_are_observed(diagnostic_database):
    task()
    claimed=durable_tasks.claim_task('daily_queue')
    task()
    class PublicationDatabase:
        def __init__(self,connection):
            self.connection=connection
        def execute(self,statement,parameters=()):
            return self.connection.execute(statement.replace('FOR UPDATE',''),parameters)
    with database.connection() as connection:
        assert not durable_tasks.lock_current_slice(PublicationDatabase(connection),claimed)
        connection.execute('DELETE FROM background_tasks')
        assert not durable_tasks.complete_task_slice_in_transaction(connection,claimed)
    assert counts().stale_results==2


def test_engine_defense_expired_lease_reclaim_and_claim_are_observed(monkeypatch):
    from app import threat_analysis_commands
    recorded=[]
    monkeypatch.setattr(threat_analysis_commands,'increment',lambda _database,kind,identity,**deltas: recorded.append((kind,identity,deltas)))
    class Cursor:
        def __init__(self,row=None):
            self.row=row
        def fetchone(self):
            return self.row
    class ClaimDatabase:
        def execute_native(self,statement,parameters=()):
            if statement.startswith("UPDATE threat_analysis_requests SET state='queued'"):
                return Cursor({'id':'expired'})
            if statement.startswith('SELECT request.id'):
                return Cursor({'id':'claimed','request_json':'{}'})
            return Cursor()
    result=threat_analysis_commands.claim_threat_analysis(ClaimDatabase(),{})
    assert result['job']['id']=='claimed'
    assert recorded==[('engine_defense','expired',{'lease_expiries':1,'lease_reclaims':1,'generation_restarts':1}),
                      ('engine_defense','claimed',{'claims':1})]


def test_background_runbook_snapshots_validate_without_private_fields():
    from pathlib import Path
    examples=json.loads((Path(__file__).resolve().parents[2]/'docs/background-diagnostics-example.json').read_text())
    assert [example['period'] for example in examples]==['quiet','training','drain']
    for example in examples:
        snapshot=BackgroundDiagnostics.model_validate(example['snapshot'])
        assert snapshot.available
        assert all(entry.kind in {'engine_game','repertoire_priority','game_analysis_publish'} for entry in snapshot.counters)


def test_background_postgres_numeric_aggregates_preserve_strict_public_schema(diagnostic_database,monkeypatch):
    from decimal import Decimal
    from types import SimpleNamespace
    from app.services.background_metrics import COUNT_NAMES
    row=task()
    with database.connection() as connection:
        connection.execute('UPDATE background_tasks SET age_origin_estimated=1 WHERE id=?',(row['id'],))
    original_read=background_diagnostics.read_connection
    class NumericAggregates:
        def __init__(self,connection):
            self.connection=connection
        def set_progress_handler(self,*arguments):
            return self.connection.set_progress_handler(*arguments)
        def execute(self,statement,parameters=()):
            rows=self.connection.execute(statement,parameters).fetchall()
            if 'SUM(' in statement:
                rows=[{key:Decimal(value) if value is not None and (key in COUNT_NAMES or key=='estimated') else value
                       for key,value in dict(result).items()} for result in rows]
            return SimpleNamespace(fetchall=lambda:rows)
    @contextmanager
    def numeric_read():
        with original_read() as connection:
            yield NumericAggregates(connection)
    monkeypatch.setattr(background_diagnostics,'read_connection',numeric_read)
    result=background_diagnostics.snapshot()
    assert result.available
    assert result.queues[0].estimated_age_count==1
    assert result.counters[0].counts.generations_started==1
    assert type(result.counters[0].counts.generations_started) is int


def test_background_delivery_preflight_is_classified_and_measured_before_handler(diagnostic_database,monkeypatch):
    from app import tasks
    task()
    claimed=durable_tasks.claim_task('daily_queue')
    observed=[]
    def preflight(delivery):
        assert tasks.activity_gate.in_background
        assert tasks.activity_gate.current_job==('daily_queue',claimed['id'])
        assert background_runtime._current.get() is not None
        observed.append('preflight')
        return True
    def handler(delivery):
        assert tasks.activity_gate.in_background
        observed.append('handler')
        return False
    monkeypatch.setattr(tasks,'current_delivery',preflight)
    monkeypatch.setattr(tasks,'execute_postgres_queue_refresh_slice',handler)
    assert tasks.execute_background_slice.run(claimed) is False
    assert observed==['preflight','handler']


def test_background_delivery_preflight_uses_primary_and_read_only_transaction(diagnostic_database,monkeypatch):
    from app import postgres_store
    from types import SimpleNamespace
    task()
    claimed=durable_tasks.claim_task('daily_queue')
    pools=[]
    statements=[]
    class Primary:
        def execute(self,statement,parameters=()):
            statements.append(statement)
            assert database.activity_gate.active_background_sections==1
            assert database.activity_gate.in_background
            return SimpleNamespace(fetchone=lambda:claimed)
    class Pool:
        @contextmanager
        def connection(self,**kwargs):
            yield Primary()
    def select_pool(read_only):
        pools.append(read_only)
        assert not read_only, 'preflight must never consult a replica'
        return Pool()
    monkeypatch.setattr(postgres_store,'configured',lambda:True)
    monkeypatch.setattr(postgres_store,'_pool',select_pool)
    with database.activity_gate.background_job('daily_queue',claimed['id']):
        assert durable_tasks.current_delivery(claimed)
    assert pools==[False]
    assert 'SET TRANSACTION READ ONLY' in statements
    assert any('transaction_timeout' in statement for statement in statements)


def test_background_delivery_preflight_waits_for_foreground_admission(diagnostic_database,monkeypatch):
    task()
    claimed=durable_tasks.claim_task('daily_queue')
    started=threading.Event()
    query_opened=threading.Event()
    finished=threading.Event()
    observed=[]
    original_read=database.read_connection
    @contextmanager
    def observed_read():
        query_opened.set()
        with original_read() as connection:
            yield connection
    monkeypatch.setattr(database,'read_connection',observed_read)
    monkeypatch.setattr(durable_tasks,'read_connection',observed_read)
    def worker():
        try:
            with background_runtime.measure_handler('daily_queue') as measurement, database.activity_gate.background_job('daily_queue',claimed['id']):
                started.set()
                observed.append(durable_tasks.current_delivery(claimed))
                observed.append(measurement.sample().admission_wait_seconds)
        finally:
            finished.set()
    thread=threading.Thread(target=worker)
    try:
        with database.activity_gate.foreground():
            thread.start()
            assert started.wait(1)
            assert not query_opened.wait(0.05), 'preflight bypassed foreground admission'
        assert finished.wait(1)
    finally:
        thread.join(1)
    assert observed[0] is True
    assert observed[1]>0


@pytest.mark.parametrize('fail_section',[False,True])
def test_background_diagnostic_redis_io_never_runs_under_database_reservation(diagnostic_database,monkeypatch,fail_section):
    lease_active=[False]
    violations=[]
    samples=[]
    clock=[0.0]
    gate=database.activity_gate
    @contextmanager
    def shared_lease():
        lease_active[0]=True
        try:
            yield
        finally:
            lease_active[0]=False
    class DiagnosticRedis:
        def set(self,key,value,ex):
            if gate.active_background_sections or lease_active[0]:
                violations.append((gate.active_background_sections,lease_active[0]))
            assert ex==15
            samples.append(json.loads(value))
        def mget(self,keys):
            return [json.dumps(samples[-1])]+[None]*(len(keys)-1)
    monkeypatch.setattr(background_runtime.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(background_runtime.redis_admission_gate,'configured',lambda:True)
    monkeypatch.setattr(background_runtime.redis_admission_gate,'background_lease',shared_lease)
    monkeypatch.setattr(background_runtime.redis_admission_gate,'foreground_present',lambda:False)
    monkeypatch.setattr(background_runtime,'_diagnostic_client',lambda:DiagnosticRedis())
    with background_runtime.measure_handler('daily_queue') as measurement, gate.background_job('daily_queue','synthetic'):
        clock[0]=6
        try:
            with database.background_read_connection():
                assert gate.active_background_sections==1
                assert lease_active[0]
                assert measurement.stage=='database'
                clock[0]=12
                measurement.publish(force=True) # Even a future misplaced force cannot do network I/O.
                if fail_section:
                    raise RuntimeError('synthetic read failure')
        except RuntimeError:
            assert fail_section
        assert gate.active_background_sections==0
        assert not lease_active[0]
    assert violations==[]
    assert any(sample['stage']=='database' for sample in samples)
    assert samples[-1]['stage']=='idle'
    assert background_runtime.snapshot().available


def test_background_admission_timing_excludes_diagnostic_publication(monkeypatch):
    clock=[0.0]
    monkeypatch.setattr(background_runtime.time,'monotonic',lambda:clock[0])
    def publish(measurement,**kwargs):
        if measurement.stage=='foreground_admission':
            clock[0]+=2
    monkeypatch.setattr(background_runtime.RuntimeMeasurement,'publish',publish)
    with background_runtime.measure_handler('daily_queue') as measurement:
        with background_runtime.admission_wait():
            clock[0]+=3
        assert measurement.sample().admission_wait_seconds==3
