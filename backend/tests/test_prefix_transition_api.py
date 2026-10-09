"""Issue 79: typed read-only boundary, snapshot capture, and response fencing."""
from dataclasses import replace
import asyncio
import json
import re

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import prefix_transition_api as api
from app.services.prefix_evaluation import snapshot_identity
from app.services.prefix_transition import canonical_json
from test_prefix_transition import prepared_snapshot, changed_snapshot


@pytest.fixture
def prepared(monkeypatch):
    state = {'snapshot': prepared_snapshot()}
    monkeypatch.setattr(api.structural.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(api.structural, 'check_available', lambda *_args: None)
    monkeypatch.setattr(api.structural, 'load_snapshot', lambda *_args: state['snapshot'].source)
    monkeypatch.setattr(api, 'discover_memberships', lambda *_args: tuple(row['card_id'] for row in state['snapshot'].rows('repertoire_cards')))
    monkeypatch.setattr(api, 'load_transition_snapshot', lambda *_args: state['snapshot'])
    application = FastAPI()
    application.include_router(api.router)
    return state, TestClient(application)


def request_payload(state, **changes):
    return dict(snapshot_id=snapshot_identity(state['snapshot'].source), selected_line_ids=['caro'],
                candidate_depths={'caro': 2}) | changes


def test_issue79_http_plan_is_typed_deterministic_and_has_no_application_authority(prepared):
    state, client = prepared
    payload = request_payload(state)
    response = client.post('/api/repertoires/rep/prefix-transition/plan', json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['dry_run'] and result['status'] == 'ready'
    assert result['plan_id'] and result['transition_snapshot_id']
    assert result == client.post('/api/repertoires/rep/prefix-transition/plan', json=payload).json()
    assert 'PrefixTransitionPlan' in client.get('/openapi.json').json()['components']['schemas']


@pytest.mark.parametrize('changes', [dict(candidate_depths={'caro': True}), dict(candidate_depths={'caro': '2'}),
                                    dict(candidate_depths={}), dict(selected_line_ids=['caro', 'caro']),
                                    dict(candidate_depths={'caro': 4}), dict(apply=True)])
def test_issue79_http_invalid_or_lengthening_requests_fail_without_partial_plans(prepared, changes):
    state, client = prepared
    response = client.post('/api/repertoires/rep/prefix-transition/plan', json=request_payload(state, **changes))
    assert response.status_code in {409, 422}
    assert response.json()['detail']['code'] in {'invalid_selection', 'prefix_lengthening'}
    assert 'cards' not in response.json()


@pytest.mark.parametrize('phase', ['discovery', 'classification', 'source_token'])
def test_issue79_http_rejects_stale_sources_and_state_changes_during_planning(prepared, monkeypatch, phase):
    state, client = prepared
    payload = request_payload(state)
    original = api.iter_transition_plan
    if phase == 'classification':
        def mutate_after_plan(*args):
            result = yield from original(*args)
            cards = state['snapshot'].rows('cards')
            cards[0]['revision'] += 1
            state['snapshot'] = changed_snapshot(state['snapshot'], 'cards', cards)
            return result
        monkeypatch.setattr(api, 'iter_transition_plan', mutate_after_plan)
    else:
        updated = replace(state['snapshot'], source=replace(state['snapshot'].source, graph_generation=2))
        if phase == 'source_token':
            state['snapshot'] = updated
        else:
            monkeypatch.setattr(api, 'load_transition_snapshot', lambda *_args: updated)
    response = client.post('/api/repertoires/rep/prefix-transition/plan', json=payload)
    assert response.status_code == 409
    assert response.json()['detail']['code'] in {'stale_snapshot', 'stale_plan'}
    assert 'cards' not in response.json()


def test_issue79_http_conflicting_authored_membership_returns_complete_blocked_plan(prepared):
    state, client = prepared
    links = state['snapshot'].rows('repertoire_cards')
    old_id = state['snapshot'].source.published_steps[0].card_id
    next(link for link in links if link['card_id'] == old_id)['canonical_route_source'] = 1
    state['snapshot'] = changed_snapshot(state['snapshot'], 'repertoire_cards', links)
    response = client.post('/api/repertoires/rep/prefix-transition/plan', json=request_payload(state))
    assert response.status_code == 200 and response.json()['status'] == 'blocked'
    assert response.json()['blockers'][0]['code'] == 'authored_membership'


def test_issue79_http_outage_and_foreground_preemption_are_retryable_without_false_success(prepared, monkeypatch):
    state, client = prepared
    def unavailable(*_args):
        raise api.psycopg.OperationalError('No reader available')
    monkeypatch.setattr(api, 'load_transition_snapshot', unavailable)
    response = client.post('/api/repertoires/rep/prefix-transition/plan', json=request_payload(state))
    assert response.status_code == 503 and response.headers['Retry-After'] == '1'
    monkeypatch.setattr(api.structural, 'check_available', lambda *_args: (_ for _ in ()).throw(api.structural.diagnostic_error('evaluation_busy', 'Foreground study is active.', 503)))
    response = client.post('/api/repertoires/rep/prefix-transition/plan', json=request_payload(state))
    assert response.status_code == 503 and 'plan_id' not in response.json()


@pytest.mark.parametrize('oversized', ['rows', 'bytes', None])
def test_issue79_capture_bounds_raw_transfer_and_hashes_only_after_transaction_close(monkeypatch, oversized):
    fixture = prepared_snapshot()
    active_read = False
    queries = []
    class Cursor:
        def __init__(self, value): self.value = value
        def fetchone(self): return self.value
        def fetchall(self): return self.value
    class Database:
        def execute_native_batch(self, statements):
            return [self.execute_native(query, parameters) for query, parameters in statements]

        def execute_native(self, query, parameters):
            assert active_read
            queries.append(query)
            name = re.search(r'FROM (\w+)', query).group(1)
            rows = fixture.rows(name)
            if 'octet_length(row_to_json(bounded)::text)' in query:
                table_names = [re.search(r'FROM (\w+)', component).group(1)
                               for component in re.findall(r'FROM \((SELECT .*?)\) bounded', query)]
                table_counts = [len(fixture.rows(name)) for name in table_names]
                table_bytes = [len(canonical_json(fixture.rows(name))) for name in table_names]
                if oversized == 'rows': table_counts[0] = api.MAX_TRANSITION_ROWS + 1
                if oversized == 'bytes': table_bytes[0] = api.MAX_TRANSITION_BYTES + 1
                return Cursor(dict(count=sum(table_counts), bytes=sum(table_bytes),
                                   table_counts=table_counts, table_bytes=table_bytes))
            return Cursor(rows)
    def loader(_identifier, _deadline, *, capture):
        nonlocal active_read
        active_read = True
        try:
            result = capture(Database())
        finally:
            active_read = False
        return fixture.source, result
    def encode_after_close(value):
        assert not active_read
        return canonical_json(value)
    monkeypatch.setattr(api.structural, 'load_snapshot', loader)
    monkeypatch.setattr(api.structural, 'check_available', lambda *_args: None)
    monkeypatch.setattr(api, 'canonical_json', encode_after_close)
    if oversized:
        with pytest.raises(api.PrefixEvaluationError) as error:
            api.load_transition_snapshot('rep', fixture.lookup_card_ids, fixture.study_day, 999999999)
        assert error.value.code == 'limit_exceeded'
        assert len(queries) == 1  # No raw oversized payload was transferred.
    else:
        captured = api.load_transition_snapshot('rep', fixture.lookup_card_ids, fixture.study_day, 999999999)
        assert captured.rows('cards') == fixture.rows('cards')
        assert all(query.startswith(('SELECT ', 'WITH snapshot_scope AS MATERIALIZED (SELECT ')) for query in queries)


def test_issue79_runtime_guard_is_background_query_only_without_command_dispatch(monkeypatch):
    from app import main, database
    from starlette.requests import Request
    from starlette.responses import Response
    monkeypatch.setattr(api.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(api.structural.redis_admission_gate, 'configured', lambda: False)
    seen = []
    async def probe():
        async def downstream(_request):
            seen.append((api.structural.activity_gate.in_background, database._query_only_request.get()))
            return Response(status_code=204)
        request = Request({'type': 'http', 'method': 'POST', 'path': '/api/repertoires/rep/prefix-transition/plan',
                           'headers': [], 'query_string': b''})
        return await main.prioritize_foreground_requests(request, downstream)
    assert asyncio.run(probe()).status_code == 204
    assert seen == [(True, True)]


@pytest.mark.parametrize('message,expected_requests', [
    ('Study work is active. Retry the diagnostic when study is idle.', 2),
    ('The diagnostic read service is temporarily unavailable. Retry after study work settles.', 1),
])
def test_issue79_postgres_rehearsal_coordinates_only_explicit_foreground_rejection(monkeypatch, message, expected_requests):
    """Periodic product health leases cannot masquerade as nondeterministic plans."""
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace
    from app.services import redis_admission_gate

    script_path = Path(__file__).resolve().parents[2] / 'scripts/check_postgres_opening_segmentation.py'
    specification = importlib.util.spec_from_file_location('issue79_postgres_rehearsal', script_path)
    rehearsal = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(rehearsal)
    monkeypatch.setattr(redis_admission_gate, 'foreground_present', lambda: False)
    busy_body = {'detail': {'code': 'evaluation_busy', 'message': message}}
    busy = SimpleNamespace(status_code=503, headers={'Retry-After': '1'}, text=json.dumps(busy_body), json=lambda: busy_body)
    ready_body = {'status': 'ready', 'plan_id': 'same-snapshot-plan'}
    ready = SimpleNamespace(status_code=200, headers={}, text=json.dumps(ready_body), json=lambda: ready_body)
    responses = iter((busy, ready))
    requests = []
    def post(path, *, json):
        requests.append((path, json))
        return next(responses)
    response = rehearsal.post_transition_when_foreground_idle(SimpleNamespace(post=post), '/plan', {'snapshot_id': 'same'})
    assert len(requests) == expected_requests
    assert response is (ready if expected_requests == 2 else busy)



def test_issue79_rehearsal_never_replays_a_started_calculation_after_foreground_preemption(monkeypatch):
    import importlib.util
    from pathlib import Path
    from threading import Event
    from types import SimpleNamespace
    from app.services import redis_admission_gate

    script_path = Path(__file__).resolve().parents[2] / 'scripts/check_postgres_opening_segmentation.py'
    specification = importlib.util.spec_from_file_location('issue79_started_plan_rehearsal', script_path)
    rehearsal = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(rehearsal)
    monkeypatch.setattr(redis_admission_gate, 'foreground_present', lambda: False)
    calculation_started = Event()
    busy_body = {'detail': {'code': 'evaluation_busy', 'message': 'Study work is active. Retry the diagnostic when study is idle.'}}
    busy = SimpleNamespace(status_code=503, headers={'Retry-After': '1'}, text=json.dumps(busy_body), json=lambda: busy_body)
    ready = SimpleNamespace(status_code=200, headers={}, text='fresh plan', json=lambda: {'status': 'ready'})
    requests = []
    def post(path, *, json):
        requests.append((path, json))
        calculation_started.set()
        return busy if len(requests) == 1 else ready
    response = rehearsal.post_transition_when_foreground_idle(SimpleNamespace(post=post), '/plan', {'snapshot_id': 'original'},
                                                              calculation_started=calculation_started)
    assert response is busy
    assert len(requests) == 1

def test_issue79_rehearsal_retries_foreground_preemption_with_a_new_review_identity(monkeypatch):
    """Every restarted capture must observe a new review, rather than an idempotent replay."""
    import importlib.util
    from contextlib import contextmanager
    from pathlib import Path
    from types import SimpleNamespace
    import fastapi.testclient
    from app import prefix_evaluation_api

    script_path = Path(__file__).resolve().parents[2] / 'scripts/check_postgres_opening_segmentation.py'
    specification = importlib.util.spec_from_file_location('issue79_review_identity_rehearsal', script_path)
    rehearsal = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(rehearsal)
    source = {'snapshot_id': 'structural-source'}
    review_identities = []
    saved_reviews = set()
    experiment_snapshots = []

    def response(status, body):
        return SimpleNamespace(status_code=status, headers={'Retry-After': '1'},
                               text=json.dumps(body), json=lambda: body)

    ready = response(200, {'status': 'ready', 'plan_id': 'current-plan'})
    @contextmanager
    def connection(**_options):
        yield SimpleNamespace(execute_native=lambda *_args: SimpleNamespace(
            fetchone=lambda: (123, 'on', 'repeatable read', '50ms')))

    @contextmanager
    def observer(*_args, **_options):
        yield SimpleNamespace(execute=lambda *_args: SimpleNamespace(
            fetchall=lambda: [{'state': 'idle', 'xact_start': None}]))

    def review(_database, _card_id, _outcome, **options):
        # Model the production source_kind/source_ref receipt contract: replay is a no-op.
        review_identities.append(options['source_ref'])
        idempotent = options['source_ref'] in saved_reviews
        saved_reviews.add(options['source_ref'])
        return {'idempotent': idempotent}

    def calculation(*_args):
        yield None

    def post(_client, _path, _payload, *, calculation_started=None):
        if calculation_started is None:
            return ready
        captured_reviews = frozenset(saved_reviews)
        experiment_snapshots.append(captured_reviews)
        with rehearsal.postgres_store.connection(read_only=True, background=True, repeatable_read=True):
            pass
        # Execute the real rehearsal's deferred calculation on its worker thread.
        list(api.iter_transition_plan())
        if len(experiment_snapshots) == 1:
            return response(503, {'detail': {'code': 'evaluation_busy',
                'message': 'Study work is active. Retry the diagnostic when study is idle.'}})
        if frozenset(saved_reviews) != captured_reviews:
            return response(409, {'detail': {'code': 'stale_plan'}})
        return ready

    monkeypatch.setattr(fastapi.testclient, 'TestClient', lambda _app: object())
    monkeypatch.setattr(rehearsal, 'idle_prefix_response', lambda *_args: response(200, source))
    monkeypatch.setattr(rehearsal, 'post_transition_when_foreground_idle', post)
    monkeypatch.setattr(rehearsal, 'test_issue79_pending_command_bindings_are_accounted_before_delivery', lambda *_args: None)
    monkeypatch.setattr(rehearsal.postgres_store, 'connection', connection)
    monkeypatch.setattr(rehearsal.psycopg, 'connect', observer)
    monkeypatch.setattr(rehearsal, 'apply_scheduling_review', review)
    monkeypatch.setattr(api, 'iter_transition_plan', calculation)
    monkeypatch.setattr(prefix_evaluation_api, 'load_snapshot', lambda *_args: source)
    monkeypatch.setattr(prefix_evaluation_api, 'snapshot_identity', lambda snapshot: snapshot['snapshot_id'])
    monkeypatch.setenv('TEMPO_DATABASE_READ_URL', 'test-reader')
    rehearsal.test_issue79_readonly_planner_foreground_concurrency_and_stale_replay(
        'repertoire', [{'id': 'line'}], ['card'], lambda: frozenset(saved_reviews))
    assert len(experiment_snapshots) == 2
    assert len(review_identities) == len(set(review_identities)) == 2
    assert experiment_snapshots == [frozenset(), frozenset([review_identities[0]])]


def test_pr102_snapshot_size_checks_use_one_statement_with_unchanged_native_rows(monkeypatch):
    from types import SimpleNamespace
    captured_fixture = prepared_snapshot()
    statements = []
    def execute(query, parameters):
        statements.append(query)
        table_name = re.search(r'FROM (\w+)', query).group(1)
        table_rows = captured_fixture.rows(table_name)
        if 'octet_length(row_to_json(bounded)::text)' in query:
            assert len(parameters) == 2, 'Repeated lookup arrays must be bound once for metadata'
            table_names = [re.search(r'FROM (\w+)', component).group(1)
                           for component in re.findall(r'FROM \((SELECT .*?)\) bounded', query)]
            table_counts = [len(captured_fixture.rows(name)) for name in table_names]
            table_bytes = [len(canonical_json(captured_fixture.rows(name))) for name in table_names]
            return SimpleNamespace(fetchone=lambda: dict(count=sum(table_counts), bytes=sum(table_bytes),
                                                        table_counts=table_counts, table_bytes=table_bytes))
        return SimpleNamespace(fetchall=lambda: table_rows)
    monkeypatch.setattr(api.structural, 'load_snapshot', lambda _identifier, _deadline, *, capture: (
        captured_fixture.source, capture(SimpleNamespace(execute_native=execute, execute_native_batch=lambda statements: [execute(query, parameters) for query, parameters in statements]))))
    monkeypatch.setattr(api.structural, 'check_available', lambda *_args: None)
    captured = api.load_transition_snapshot('rep', captured_fixture.lookup_card_ids, captured_fixture.study_day, 999999999)
    assert captured.rows('cards') == captured_fixture.rows('cards')
    assert captured.rows('repertoire_cards') == captured_fixture.rows('repertoire_cards')
    size_checks = [query for query in statements if 'octet_length(row_to_json(bounded)::text)' in query]
    assert len(size_checks) == 1, f'Preparation executed {len(size_checks)} separate size queries'


def test_pr102_native_snapshot_batch_preserves_digest_evidence_and_read_order():
    from contextlib import contextmanager
    from decimal import Decimal
    from types import SimpleNamespace
    from app.postgres_store import PostgresConnection, TempoRow
    from app.snapshot_reads import RecordingReader

    pipeline_active = False
    pipeline_events = []
    statements = []
    native_value = Decimal('1.234567890123456789')
    @contextmanager
    def pipeline():
        nonlocal pipeline_active
        pipeline_events.append('enter')
        pipeline_active = True
        try:
            yield
        finally:
            pipeline_active = False
            pipeline_events.append('exit')
    def execute(query, parameters):
        assert pipeline_active
        statements.append((query, parameters))
        assert 'encode(sha256(convert_to(row_to_json(captured)::text' in query
        def fetchall():
            assert not pipeline_active
            return [TempoRow(('id', 'value', '__transition_evidence'), (parameters[0], native_value, 'digest'))]
        return SimpleNamespace(fetchall=fetchall)
    reads = []
    reader = RecordingReader(PostgresConnection(SimpleNamespace(pipeline=pipeline, execute=execute)), reads)
    queries = [('SELECT id,value FROM cards WHERE id=%s', ('first',)),
               ('SELECT id,value FROM cards WHERE id=%s', ('second',))]
    cursors = reader.execute_native_batch(queries)
    assert pipeline_events == ['enter', 'exit'] and len(statements) == 2
    rows = [cursor.fetchall()[0] for cursor in cursors]
    assert [row['id'] for row in rows] == ['first', 'second']
    assert all(row['value'] is native_value for row in rows)
    assert [(read.query, read.parameters, read.rows, read.uses_sha256_evidence) for read in reads] == [
        (query, parameters, ('digest',), True) for query, parameters in queries]


@pytest.mark.parametrize('stale_link', [False, True])
def test_pr102_snapshot_batch_respects_cumulative_limits_and_stale_link_precedence(monkeypatch, stale_link):
    from types import SimpleNamespace
    fixture = prepared_snapshot()
    batches = []
    def execute(query, parameters):
        assert 'octet_length(row_to_json(bounded)::text)' in query
        table_counts = [0, 0, 1, api.MAX_TRANSITION_ROWS] + [0] * 16
        return SimpleNamespace(fetchone=lambda: dict(count=sum(table_counts), bytes=0,
                                                    table_counts=table_counts, table_bytes=[0] * 20))
    def batch(statements):
        batches.append(statements)
        assert len(statements) == 3  # Never transfer the first oversized table or its suffix.
        card_identifier = 'not-in-approved-lookup' if stale_link else fixture.lookup_card_ids[0]
        return [SimpleNamespace(fetchall=lambda: []), SimpleNamespace(fetchall=lambda: []),
                SimpleNamespace(fetchall=lambda: [{'card_id': card_identifier, 'repertoire_id': 'rep'}])]
    monkeypatch.setattr(api.structural, 'load_snapshot', lambda _identifier, _deadline, *, capture: (
        fixture.source, capture(SimpleNamespace(execute_native=execute, execute_native_batch=batch))))
    with pytest.raises(api.PrefixEvaluationError) as error:
        api.load_transition_snapshot('rep', fixture.lookup_card_ids, fixture.study_day, 999999999)
    assert error.value.code == ('stale_snapshot' if stale_link else 'limit_exceeded')
    assert len(batches) == 1
