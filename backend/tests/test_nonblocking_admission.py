"""Admission denial releases capacity and unavailable evidence never admits."""
from contextlib import contextmanager

import pytest
import redis

from app.services import redis_admission_gate
from app.services.activity_gate import ApplicationActivityGate, BackgroundAdmissionDeferred


@pytest.mark.parametrize('response',[0,redis.ConnectionError('controlled outage')])
def test_redis_background_admission_checks_once_and_never_sleeps(monkeypatch, response):
    calls = []
    class Server:
        def eval(self, *args):
            calls.append(args)
            if isinstance(response, Exception):
                raise response
            return response
        def zrem(self, *args):
            pytest.fail('unowned lease must not be released')
    monkeypatch.setattr(redis_admission_gate, 'client', lambda: Server())
    monkeypatch.setattr(redis_admission_gate.time, 'sleep', lambda *args: pytest.fail('occupied worker'))
    with pytest.raises(BackgroundAdmissionDeferred):
        with redis_admission_gate.background_lease():
            pytest.fail('denied lease')
    assert len(calls) == 1


def test_admission_redis_outage_is_unavailable_and_recovers(monkeypatch):
    monkeypatch.setattr(redis_admission_gate, 'configured', lambda: True)
    def outage():
        raise redis.ConnectionError('controlled outage')
    monkeypatch.setattr(redis_admission_gate, 'foreground_present', outage)
    gate = ApplicationActivityGate()
    with pytest.raises(BackgroundAdmissionDeferred, match='unavailable'):
        gate.check_background_admission()
    monkeypatch.setattr(redis_admission_gate, 'foreground_present', lambda: False)
    gate.check_background_admission()


def test_control_bookkeeping_does_not_claim_shared_admission(monkeypatch):
    gate = ApplicationActivityGate()
    monkeypatch.setattr(redis_admission_gate, 'configured', lambda: True)
    @contextmanager
    def denied():
        raise BackgroundAdmissionDeferred('foreground')
        yield
    monkeypatch.setattr(redis_admission_gate, 'background_lease', denied)
    with gate.background_job('test', 'control', yielding=True), gate.background_control():
        with gate.background_database_section():
            assert gate.active_background_sections == 1
    assert gate.active_background_sections == 0
    with pytest.raises(BackgroundAdmissionDeferred):
        with gate.background_database_section():
            pytest.fail('control context leaked')


def test_background_http_admission_reports_waiting_and_retry_without_false_success(monkeypatch):
    from contextlib import contextmanager
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: True)
    @contextmanager
    def denied():
        raise BackgroundAdmissionDeferred('Waiting for foreground activity')
        yield
    monkeypatch.setattr(main, 'background_read_connection', denied)
    response = TestClient(main.app).get('/api/repertoire-coverage/maia/available',
                                       headers={'X-Tempo-Work-Class':'background'})
    assert response.status_code == 503 and response.headers['Retry-After'] == '1'
    assert response.json() == {'detail':'Waiting for foreground activity'}


def test_short_control_receipt_is_admitted_during_existing_background_reservation(monkeypatch):
    monkeypatch.setattr(redis_admission_gate, 'configured', lambda: False)
    gate = ApplicationActivityGate()
    with gate.background_job('test', 'reservation', yielding=True):
        with gate.background_database_section():
            with gate.background_control(), gate.background_database_section():
                assert gate.active_background_sections == 2
            assert gate.active_background_sections == 1
            with pytest.raises(BackgroundAdmissionDeferred):
                with gate.background_database_section():
                    pytest.fail('discretionary section must yield')
    assert gate.active_background_sections == 0


@pytest.mark.parametrize('request_state,stored_lease_id,expected_allowed', [
    ('leased', 'current', True), ('leased', 'obsolete', False), ('complete', 'current', False),
])
def test_defensive_control_uses_primary_api_reader_without_writer_credentials(
    monkeypatch, request_state, stored_lease_id, expected_allowed,
):
    from types import SimpleNamespace
    from app import main, postgres_store
    monkeypatch.delenv('TEMPO_DATABASE_WRITE_URL', raising=False)
    monkeypatch.delenv('TEMPO_REDIS_URL', raising=False)
    monkeypatch.delenv('TEMPO_FOREGROUND_ACTIVITY_URL', raising=False)
    monkeypatch.setattr(postgres_store, 'configured', lambda: True)
    queries = []
    @contextmanager
    def reader(**options):
        assert options == {'read_only': True, 'background': True}, 'API control probe requested writer credentials'
        def execute(statement, parameters):
            queries.append((statement, parameters))
            return SimpleNamespace(fetchone=lambda: {
                'state': request_state, 'lease_id': stored_lease_id, 'search_allowed': 1,
            })
        yield SimpleNamespace(execute=execute)
    monkeypatch.setattr(postgres_store, 'connection', reader)
    assert main.defensive_engine_control('request', 'current') == {
        'foreground_active': False, 'search_allowed': expected_allowed,
    }
    assert len(queries) == 1 and queries[0][1] == ('request',)


@pytest.mark.parametrize('receipt_exists', [True, False])
def test_background_receipt_read_is_prompt_during_foreground_without_false_success(monkeypatch, receipt_exists):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from app import command_gateway, postgres_store
    from app.services.activity_gate import activity_gate
    monkeypatch.delenv('TEMPO_REDIS_URL', raising=False)
    monkeypatch.delenv('TEMPO_FOREGROUND_ACTIVITY_URL', raising=False)
    monkeypatch.setattr(postgres_store, 'configured', lambda: True)
    receipt = ('coverage.maia.submit', 'digest', 'retrying', None, None, 0,
               None, None, None, '{}', 0, 0) if receipt_exists else None
    observations = []
    def read(statement, parameters):
        assert 'FROM operation_receipts WHERE operation_id=%s' in statement
        assert parameters == ('saved-receipt',)
        return SimpleNamespace(fetchone=lambda: receipt)
    @contextmanager
    def connection(**options):
        observations.append(options)
        yield SimpleNamespace(raw=SimpleNamespace(execute=read))
    monkeypatch.setattr(postgres_store, 'connection', connection)
    with activity_gate.foreground(), activity_gate.background_request():
        result = command_gateway.read_operation('saved-receipt', background=True)
        assert result['state'] == ('retrying' if receipt_exists else 'unknown')
        assert 'response' not in result
        assert activity_gate.active_background_sections == 0
        assert not activity_gate.in_background_control
    assert observations == [{'read_only': True, 'background': True}]


def test_prefix_preparation_foreground_denial_yields_before_writer_and_preserves_http_contract(monkeypatch):
    from app import command_gateway, prefix_evaluation_api
    command_name = 'repertoire.prefix_transition.apply'
    monkeypatch.setattr(prefix_evaluation_api.redis_admission_gate, 'configured', lambda: False)
    monkeypatch.setattr(type(prefix_evaluation_api.activity_gate), 'foreground_waiting', property(lambda _: True))
    def prepare(_payload):
        prefix_evaluation_api.check_available(float('inf'))
    monkeypatch.setitem(command_gateway._preparers, command_name, prepare)
    monkeypatch.setitem(command_gateway._handlers, command_name, lambda *_args: pytest.fail('denied preparation published'))
    monkeypatch.setattr(command_gateway, '_writer_connection', lambda *_args: pytest.fail('denied preparation opened writer'))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as response:
        prepare({})
    assert response.value.status_code == 503 and response.value.detail['code'] == 'evaluation_busy'
    assert isinstance(response.value, prefix_evaluation_api.PrefixEvaluationForegroundDeferred)
    with pytest.raises(HTTPException) as expired:
        prefix_evaluation_api.check_available(0)
    assert not isinstance(expired.value, prefix_evaluation_api.PrefixEvaluationForegroundDeferred)
    with pytest.raises(BackgroundAdmissionDeferred):
        command_gateway.execute_command('retained-apply', command_name, {})


def test_prefix_foreground_receipt_denial_does_not_consume_failure_attempts(monkeypatch):
    from contextlib import nullcontext
    from app import tasks
    command_name = 'repertoire.prefix_transition.apply'
    monkeypatch.setattr(tasks.activity_gate, 'foreground', nullcontext)
    monkeypatch.setattr(tasks, 'record_operation_attempt', lambda *_args, **_kwargs: (True, {'saved': True}, 'fenced-attempt', 1))
    def denied(*_args, **_kwargs):
        raise BackgroundAdmissionDeferred('Waiting for study')
    monkeypatch.setattr(tasks, 'execute_command', denied)
    observed = []
    monkeypatch.setattr(tasks, 'defer_operation_for_foreground', lambda *args: observed.append(args))
    monkeypatch.setattr(tasks, 'record_operation_retry', lambda *_args, **_kwargs: pytest.fail('denial spent failure budget'))
    assert tasks.execute_foreground_command.run('retained-apply', command_name, {}) is None
    assert observed == [('retained-apply', 'fenced-attempt')]


@pytest.fixture
def control_command_store(monkeypatch):
    """SQL-backed command harness; native proof owns PostgreSQL lock/budget evidence."""
    import sqlite3
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from contextlib import nullcontext
    from app import postgres_store, tasks
    from app.services.background_metrics_schema import SCHEMA

    database = sqlite3.connect(':memory:')
    database.row_factory = sqlite3.Row
    database.create_function('NOW', 0, lambda: datetime.now(timezone.utc).isoformat())
    database.executescript(SCHEMA)
    database.executescript('''
        CREATE TABLE operation_receipts(operation_id TEXT PRIMARY KEY, command_name TEXT,
            request_hash TEXT, state TEXT, response_json TEXT, error_json TEXT,
            payload_json TEXT, background INTEGER, attempt_count INTEGER DEFAULT 0,
            cycle_attempt_count INTEGER DEFAULT 0, retry_cycle INTEGER DEFAULT 0,
            attempt_token TEXT, lease_expires_at TEXT, next_retry_at TEXT,
            last_error_json TEXT, updated_at TEXT);
        CREATE TABLE imported_games(id TEXT PRIMARY KEY, analysis_state TEXT);
        CREATE TABLE game_analysis_jobs(game_id TEXT PRIMARY KEY, status TEXT,
            lease_id TEXT, lease_expires_at TEXT, analysis_version INTEGER,
            analysis_evidence_version INTEGER, idempotency_key TEXT, last_error TEXT,
            updated_at TEXT);
        CREATE TABLE game_analysis_position_reports(id TEXT PRIMARY KEY, game_id TEXT,
            state TEXT, lease_id TEXT, parent_lease_id TEXT, lease_expires_at TEXT,
            request_json TEXT, report_json TEXT, last_error TEXT, updated_at TEXT);
        CREATE TABLE game_analysis_publications(game_id TEXT PRIMARY KEY,
            analysis_version INTEGER, analysis_evidence_version INTEGER, prepared_json TEXT,
            next_ply INTEGER, status TEXT, result_json TEXT, last_error TEXT, updated_at TEXT);
        CREATE TABLE background_tasks(id TEXT PRIMARY KEY, kind TEXT,
            deduplication_key TEXT, payload_json TEXT);
    ''')
    database.execute("INSERT INTO imported_games VALUES('game','analyzing')")
    database.execute("INSERT INTO game_analysis_jobs(game_id,status,lease_id,analysis_version,"
                     "analysis_evidence_version) VALUES('game','leased','parent-lease',2,1)")
    database.execute("INSERT INTO game_analysis_position_reports(id,game_id,state,lease_id,"
                     "parent_lease_id,request_json) VALUES('report','game','leased','position-lease',"
                     "'parent-lease','accepted-request')")
    database.commit()
    connections = []

    def execute(statement, parameters=()):
        if statement.startswith('SELECT pg_advisory_xact_lock'):
            return database.execute('SELECT 1')
        translated = statement.replace('%s', '?').replace(' FOR UPDATE', '')
        translated = translated.replace('GREATEST(', 'MAX(')
        translated = translated.replace("NOW()+INTERVAL '1 second'", "datetime(NOW(), '+1 second')")
        return database.execute(translated, tuple(
            value.isoformat() if isinstance(value, datetime) else value for value in parameters))

    @contextmanager
    def connection(**options):
        assert options == {'read_only': False, 'background': True}
        connections.append(options)
        # Explicit BEGIN lets SAVEPOINT release preserve the outer transaction.
        database.execute('BEGIN')
        try:
            yield SimpleNamespace(raw=SimpleNamespace(execute=execute), execute_native=execute)
            database.commit()
        except BaseException:
            database.rollback()
            raise

    monkeypatch.setattr(postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(postgres_store, 'connection', connection)
    monkeypatch.setattr(redis_admission_gate, 'configured', lambda: False)
    monkeypatch.delenv('TEMPO_FOREGROUND_ACTIVITY_URL', raising=False)
    monkeypatch.setattr(tasks, 'measure_handler', lambda *_args: nullcontext())
    try:
        yield database, connections
    finally:
        database.close()


def _forbid_control_analysis(monkeypatch):
    import chess.engine
    import httpx
    from app import game_analysis_commands, game_analysis_publication, main, tasks
    from app.services import game_analysis_worker, repertoire_coverage, threat_pipeline, queue_refresh_wakeup

    def prohibited(*_args, **_kwargs):
        pytest.fail('control command invoked analysis, traversal, or publication execution')

    monkeypatch.setattr(chess.engine.SimpleEngine, 'popen_uci', prohibited)
    monkeypatch.setattr(httpx.Client, 'request', prohibited)

    for module, names in (
        (game_analysis_commands, ('prepare_position_claim', 'prepare_position_report', '_positions', '_confirmed_indices')),
        (game_analysis_worker, ('build_game_evaluations', '_positions', '_confirmed_indices')),
        (main, ('build_game_evaluations', '_validated_analysis_evaluations', 'classify_swings')),
        (game_analysis_publication, ('execute_game_analysis_publication_slice', 'execute_game_analysis_followup_slice', '_slice_rows')),
        (tasks, ('execute_game_analysis_publication_slice', 'execute_game_analysis_followup_slice', 'execute_game_findings_slice')),
        (threat_pipeline, ('execute_threat_scan_slice', 'execute_threat_validation', 'validate_analysis_report')),
        (repertoire_coverage, ('discover_opponent_positions',)),
        (queue_refresh_wakeup, ('wake_queue_refresh',)),
    ):
        for name in names:
            monkeypatch.setattr(module, name, prohibited)


@pytest.mark.parametrize('replacement_parent', [False, True])
def test_position_report_control_commits_during_foreground_without_analysis(
    control_command_store, monkeypatch, replacement_parent,
):
    import json
    from app import tasks
    from app.services.activity_gate import activity_gate
    database, connections = control_command_store
    _forbid_control_analysis(monkeypatch)
    if replacement_parent:
        database.execute("UPDATE game_analysis_jobs SET lease_id='new-parent',analysis_version=3")
        database.commit()
    payload = {'report_id': 'report', 'lease_id': 'position-lease',
               'request_json': 'accepted-request', 'report': {'accepted': True}}
    with activity_gate.foreground():
        assert tasks.execute_background_command.run('position-result', 'games.analysis.position.report', payload) == {'status': 'complete'}
        assert tasks.execute_background_command.run('position-result', 'games.analysis.position.report', payload) is None
        assert not activity_gate.in_background_control
        assert activity_gate.active_background_sections == 0
    report = database.execute('SELECT state,report_json FROM game_analysis_position_reports').fetchone()
    assert report['state'] == 'complete' and json.loads(report['report_json']) == payload['report']
    assert database.execute('SELECT state FROM operation_receipts').fetchone()[0] == 'complete'
    parent = database.execute('SELECT status,lease_id,analysis_version FROM game_analysis_jobs').fetchone()
    assert tuple(parent) == (('leased', 'new-parent', 3) if replacement_parent else ('queued', None, 2))
    assert all(options['background'] for options in connections)


def test_finalize_admit_control_only_queues_publication_during_foreground(control_command_store, monkeypatch):
    import json
    from app import game_analysis_publication, tasks
    from app.services.activity_gate import activity_gate
    database, _connections = control_command_store
    _forbid_control_analysis(monkeypatch)
    enqueued = []
    def enqueue(database_adapter, kind, key, payload, *, priority):
        enqueued.append((kind, key, payload, priority))
        database_adapter.execute_native('INSERT INTO background_tasks VALUES(%s,%s,%s,%s)',
                                        ('publication-task', kind, key, json.dumps(payload)))
    monkeypatch.setattr(game_analysis_publication, 'enqueue_compact_postgres_task_in_transaction', enqueue)
    payload = {'game_id': 'game', 'analysis_version': 2, 'analysis_evidence_version': 3,
               'expected_evidence_version': 1, 'lease_id': 'parent-lease',
               'idempotency_key': 'game:2:3', 'prepared': {'evaluations': [{'already': 'computed'}]},
               'result': {'major_mistake_ply': None}}
    with activity_gate.foreground():
        assert tasks.execute_background_command.run('finalize', 'games.analysis.finalize.admit', payload) == {'status': 'preparing', 'task_id': 'publication-task'}
        assert tasks.execute_background_command.run('finalize', 'games.analysis.finalize.admit', payload) is None
        assert not activity_gate.in_background_control
    assert enqueued == [('game_analysis_publish', 'game', {'game_id': 'game', 'analysis_version': 2, 'next_ply': 0}, 75)]
    publication = database.execute('SELECT * FROM game_analysis_publications').fetchone()
    assert publication['status'] == 'queued' and publication['next_ply'] == 0
    assert json.loads(publication['prepared_json']) == payload['prepared']
    assert json.loads(publication['result_json']) == payload['result']
    assert database.execute('SELECT status,lease_id FROM game_analysis_jobs').fetchone()[:] == ('publishing', None)
    assert database.execute('SELECT state FROM operation_receipts').fetchone()[0] == 'complete'


@pytest.mark.parametrize('command_name', ['coverage.maia.submit', 'threat.analysis.report'])
def test_unbounded_result_callbacks_defer_during_foreground(control_command_store, monkeypatch, command_name):
    import json
    from app import command_gateway, tasks
    from app.services.activity_gate import activity_gate
    database, _connections = control_command_store
    monkeypatch.setitem(command_gateway._handlers, command_name,
                        lambda *_args: pytest.fail('unbounded result callback ran during foreground'))
    payload = {'accepted_result': 'retain-for-replay'}
    with activity_gate.foreground():
        assert tasks.execute_background_command.run('unbounded-result', command_name, payload) is None
        assert not activity_gate.in_background_control
        with pytest.raises(BackgroundAdmissionDeferred):
            activity_gate.check_background_admission()
    receipt = database.execute('SELECT * FROM operation_receipts').fetchone()
    assert receipt['state'] == 'retrying'
    assert receipt['attempt_count'] == receipt['cycle_attempt_count'] == 0
    assert receipt['attempt_token'] is None and receipt['error_json'] is None
    assert json.loads(receipt['payload_json']) == payload
    assert database.execute('SELECT status FROM game_analysis_jobs').fetchone()[0] == 'leased'


def test_discretionary_position_claim_still_defers_during_foreground(control_command_store, monkeypatch):
    from app import command_gateway, tasks
    from app.services.activity_gate import activity_gate
    database, _connections = control_command_store
    monkeypatch.setitem(command_gateway._handlers, 'games.analysis.position.claim',
                        lambda *_args: pytest.fail('denied position claim ran'))
    with activity_gate.foreground():
        assert tasks.execute_background_command.run('claim', 'games.analysis.position.claim', {}) is None
        with pytest.raises(BackgroundAdmissionDeferred):
            activity_gate.check_background_admission()
        assert not activity_gate.in_background_control
    assert database.execute('SELECT state,attempt_count,cycle_attempt_count FROM operation_receipts').fetchone()[:] == ('retrying', 0, 0)


@pytest.mark.parametrize('command_name,payload', [
    ('games.analysis.position.report', {'report_id': 'report', 'lease_id': 'obsolete',
                                      'request_json': 'accepted-request', 'report': {}}),
    ('games.analysis.position.report', {'report_id': 'report', 'lease_id': 'position-lease',
                                      'request_json': 'replacement-request', 'report': {}}),
    ('games.analysis.finalize.admit', {'game_id': 'game', 'analysis_version': 3,
                                    'analysis_evidence_version': 3, 'expected_evidence_version': 1,
                                    'lease_id': 'parent-lease'}),
    ('games.analysis.finalize.admit', {'game_id': 'game', 'analysis_version': 2,
                                    'analysis_evidence_version': 3, 'expected_evidence_version': 2,
                                    'lease_id': 'parent-lease'}),
    ('games.analysis.finalize.admit', {'game_id': 'game', 'analysis_version': 2,
                                    'analysis_evidence_version': 3, 'expected_evidence_version': 1,
                                    'lease_id': 'obsolete'}),
])
def test_control_publication_fences_stale_delivery(control_command_store, command_name, payload):
    from app import command_gateway, tasks
    from app.services.activity_gate import activity_gate
    database, _connections = control_command_store
    with activity_gate.foreground():
        assert tasks.execute_background_command.run('stale-result', command_name, payload) is None
        assert database.execute('SELECT state FROM operation_receipts').fetchone()[0] == 'failed'
        # A replaced operation attempt cannot enter even a safe control handler.
        with activity_gate.background_job('test', 'stale-attempt', yielding=True), activity_gate.background_control():
            allowed, saved, _token, _number = command_gateway.record_operation_attempt('old-attempt', command_name, payload, background=True)
            assert allowed
            assert command_gateway.execute_command('old-attempt', command_name, saved,
                                                   background=True, attempt_token='obsolete') is None
    assert database.execute('SELECT status,lease_id FROM game_analysis_jobs').fetchone()[:] == ('leased', 'parent-lease')
    assert database.execute('SELECT state,report_json FROM game_analysis_position_reports').fetchone()[:] == ('leased', None)
    assert database.execute('SELECT COUNT(*) FROM game_analysis_publications').fetchone()[0] == 0


@pytest.mark.parametrize('transaction_timeout', [False, True])
def test_control_result_failure_rolls_back_before_receipt_recovery(control_command_store, monkeypatch, transaction_timeout):
    from psycopg.errors import TransactionTimeout
    from app import game_analysis_commands, tasks
    from app.services.activity_gate import activity_gate
    database, _connections = control_command_store
    def failed_diagnostics(*_args, **_kwargs):
        # The handler has written the result, but the enclosing transaction must undo it.
        assert database.execute('SELECT state FROM game_analysis_position_reports').fetchone()[0] == 'complete'
        raise TransactionTimeout('controlled budget') if transaction_timeout else ValueError('controlled failure')
    monkeypatch.setattr(game_analysis_commands, 'record_engine_outcome', failed_diagnostics)
    with activity_gate.foreground():
        assert tasks.execute_background_command.run('failed-result', 'games.analysis.position.report',
            {'report_id': 'report', 'lease_id': 'position-lease',
             'request_json': 'accepted-request', 'report': {'accepted': True}}) is None
        assert not activity_gate.in_background_control
    assert database.execute('SELECT state,report_json FROM game_analysis_position_reports').fetchone()[:] == ('leased', None)
    assert database.execute('SELECT status,lease_id FROM game_analysis_jobs').fetchone()[:] == ('leased', 'parent-lease')
    assert database.execute('SELECT state FROM operation_receipts').fetchone()[0] == ('retrying' if transaction_timeout else 'failed')
