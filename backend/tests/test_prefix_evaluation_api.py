"""Issue 77: snapshot fencing and query-only diagnostic HTTP boundaries."""
from contextlib import contextmanager
from dataclasses import replace
import asyncio
import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import prefix_evaluation_api as api
from app.services.prefix_evaluation import PublishedCard, snapshot_identity
from test_prefix_evaluation import line, snapshot, CARO_B


@pytest.fixture
def prepared(monkeypatch):
    source = snapshot((line('a'), line('b', CARO_B, 2)))
    state = {'source': source}
    monkeypatch.setattr(api.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(api, 'load_snapshot', lambda *_args: state['source'])
    monkeypatch.setattr(api, 'check_available', lambda *_args: None)
    application = FastAPI(); application.include_router(api.router)
    return state, TestClient(application)


def test_issue77_http_source_and_evaluation_use_typed_snapshot_contract(prepared):
    state, client = prepared
    source = client.get('/api/repertoires/rep/prefix-evaluation/source')
    assert source.status_code == 200
    assert source.json()['snapshot_id'] == snapshot_identity(state['source'])
    result = client.post('/api/repertoires/rep/prefix-evaluation/evaluate', json={
        'snapshot_id': source.json()['snapshot_id'], 'selected_line_ids': ['a'], 'candidate_depths': {'a': 2}})
    assert result.status_code == 200 and result.json()['status'] == 'changed'
    assert result.json()['estimate_basis'].startswith('Structural counts only')
    assert 'PrefixEvaluationRequest' in client.get('/openapi.json').json()['components']['schemas']


@pytest.mark.parametrize('payload', [
    {'snapshot_id': 'x', 'selected_line_ids': ['a'], 'candidate_depths': {'a': True}},
    {'snapshot_id': 'x', 'selected_line_ids': ['a'], 'candidate_depths': {'a': '2'}},
    {'snapshot_id': '', 'selected_line_ids': []},
    {'snapshot_id': 'x', 'selected_line_ids': ['a'], 'unexpected': True},
])
def test_issue77_invalid_wire_values_have_machine_readable_errors(prepared, payload):
    _, client = prepared
    response = client.post('/api/repertoires/rep/prefix-evaluation/evaluate', json=payload)
    assert response.status_code == 422 and response.json()['detail']['code'] == 'invalid_selection'


def test_issue77_stale_snapshot_is_distinct_from_no_change_and_empty_selection(prepared):
    state, client = prepared
    token = snapshot_identity(state['source'])
    state['source'] = replace(state['source'], graph_generation=2)
    result = client.post('/api/repertoires/rep/prefix-evaluation/evaluate', json={
        'snapshot_id': token, 'selected_line_ids': [], 'candidate_depths': None})
    assert result.status_code == 409 and result.json()['detail']['code'] == 'stale_snapshot'
    assert 'whole_repertoire' not in result.json()


def test_issue77_source_change_during_calculation_rejects_entire_result(prepared, monkeypatch):
    state, client = prepared
    original = api.iter_prefix_evaluation
    def mutate_after_compute(*args):
        result = yield from original(*args)
        state['source'] = replace(state['source'], lines=(replace(state['source'].lines[0], saved_depth=2), *state['source'].lines[1:]))
        return result
    monkeypatch.setattr(api, 'iter_prefix_evaluation', mutate_after_compute)
    result = client.post('/api/repertoires/rep/prefix-evaluation/evaluate', json={
        'snapshot_id': snapshot_identity(state['source']), 'selected_line_ids': ['a']})
    assert result.status_code == 409 and result.json()['detail']['code'] == 'stale_snapshot'


def test_issue77_foreground_preemption_and_deadline_return_retryable_errors(prepared, monkeypatch):
    state, client = prepared
    monkeypatch.undo()
    monkeypatch.setattr(api.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(api, 'load_snapshot', lambda *_args: state['source'])
    monkeypatch.setattr(api.redis_admission_gate, 'configured', lambda: False)
    monkeypatch.setattr(type(api.activity_gate), 'foreground_waiting', property(lambda _: True))
    with pytest.raises(HTTPException) as active: api.check_available(999999999)
    assert active.value.status_code == 503 and active.value.detail['code'] == 'evaluation_busy'
    monkeypatch.setattr(type(api.activity_gate), 'foreground_waiting', property(lambda _: False))
    with pytest.raises(HTTPException) as expired: api.check_available(0)
    assert expired.value.detail['code'] == 'evaluation_busy'


def test_issue77_non_postgres_product_never_returns_sample_result(prepared, monkeypatch):
    _, client = prepared
    monkeypatch.setattr(api.postgres_store, 'configured', lambda: False)
    result = client.get('/api/repertoires/rep/prefix-evaluation/source')
    assert result.status_code == 409 and result.json()['detail']['code'] == 'unsupported_backend'


def test_issue77_temporary_database_failure_is_retryable_without_partial_metrics(prepared, monkeypatch):
    _, client = prepared
    def unavailable(*_args):
        raise api.psycopg.OperationalError('Diagnostic database unavailable')
    monkeypatch.setattr(api, 'load_snapshot', unavailable)
    response = client.get('/api/repertoires/rep/prefix-evaluation/source')
    assert response.status_code == 503 and response.headers['retry-after'] == '1'
    assert response.json()['detail']['code'] == 'evaluation_busy'
    assert 'lines' not in response.json()


@pytest.mark.parametrize('endpoint', ['source', 'evaluate'])
def test_issue77_endpoints_use_actual_reader_pool_without_writer_credentials(monkeypatch, endpoint):
    """Keep loader/connection/_pool real; replace only the pool's database I/O."""
    reader_url = 'postgresql://tempo_reader@postgres:5432/tempo'
    monkeypatch.setenv('TEMPO_DATABASE_READ_URL', reader_url)
    monkeypatch.delenv('TEMPO_DATABASE_WRITE_URL', raising=False)
    isolated_pools = {}
    monkeypatch.setattr(api.postgres_store, '_pools', isolated_pools)
    monkeypatch.setattr(api, 'check_available', lambda *_args: None)
    fixture = snapshot((line('a'), line('b', CARO_B)))
    cards = tuple(PublishedCard(step.card_id, step.starting_fen, step.moves, step.trained_color, 1, 0, True)
                  for step in fixture.published_steps)
    fixture = replace(fixture, presentations=cards)
    source_rows = [{**route.graph_line(), 'name': route.name} for route in fixture.lines]
    step_rows = [{**step.__dict__, 'moves_json': json.dumps(step.moves),
                  'decision_fen_keys_json': json.dumps(step.decision_fen_keys)} for step in fixture.published_steps]
    card_rows = [{'id': card.id, 'start_fen': card.start_fen, 'moves_json': json.dumps(card.moves),
                  'trained_color': card.trained_color, 'revision': card.revision,
                  'archived': card.archived, 'linked': card.linked} for card in cards]
    pool_urls = []
    transaction_statements = []
    active_reads = 0

    class Cursor:
        def __init__(self, rows): self.rows = rows
        def fetchone(self): return self.rows[0] if self.rows else None
        def fetchall(self): return self.rows

    class DatabaseIO:
        def execute(self, statement, _parameters=()):
            transaction_statements.append(statement)
            if statement.startswith('SET TRANSACTION') or 'set_config(' in statement: return Cursor([])
            if 'FROM repertoires' in statement: return Cursor([{'id': 'rep'}])
            if 'FROM opening_graph_publications' in statement:
                return Cursor([{'generation': 1, 'state': 'ready', 'task_generation': None, 'task_state': None}])
            if 'COUNT(*)' in statement: return Cursor([{'line_count': 2, 'source_bytes': 128}])
            if 'FROM repertoire_lines' in statement: return Cursor(source_rows)
            if 'FROM prefix_splits' in statement: return Cursor([])
            if 'SELECT DISTINCT step.card_id' in statement: return Cursor(card_rows)
            if 'FROM opening_graph_steps' in statement: return Cursor(step_rows)
            raise AssertionError(statement)

    class DatabasePoolIO:
        def __init__(self, conninfo, **_options): pool_urls.append(conninfo)
        @contextmanager
        def connection(self):
            nonlocal active_reads
            active_reads += 1
            try: yield DatabaseIO()
            finally: active_reads -= 1
        def close(self): pass

    monkeypatch.setattr(api.postgres_store, 'ConnectionPool', DatabasePoolIO)
    original_decode = api.json.loads
    def decode_after_connection_close(*args, **options):
        assert active_reads == 0, 'Snapshot decoding held a database connection'
        return original_decode(*args, **options)
    monkeypatch.setattr(api.json, 'loads', decode_after_connection_close)
    application = FastAPI(); application.include_router(api.router)
    client = TestClient(application)
    if endpoint == 'source':
        response = client.get('/api/repertoires/rep/prefix-evaluation/source')
        assert response.status_code == 200
        assert response.json()['snapshot_id'] == snapshot_identity(fixture)
    else:
        response = client.post('/api/repertoires/rep/prefix-evaluation/evaluate', json={
            'snapshot_id': snapshot_identity(fixture), 'selected_line_ids': ['a', 'b'],
            'candidate_depths': {'a': 2, 'b': 2}})
        assert response.status_code == 200
        assert response.json()['selected']['proposed']['metrics']['distinct_cards'] == 3
    assert pool_urls == [reader_url] and active_reads == 0
    assert transaction_statements.count('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ') == 2
    assert transaction_statements.count('SET TRANSACTION READ ONLY') == 2


def test_issue77_loader_reads_primary_repeatable_snapshot_and_closes_before_hashing(monkeypatch):
    source = snapshot((line('a'),))
    open_read = False
    statements = []
    cards = [PublishedCard(step.card_id, step.starting_fen, step.moves, step.trained_color, 1, 0, True) for step in source.published_steps]
    class Cursor:
        def __init__(self, rows): self.rows = rows
        def fetchone(self): return self.rows[0] if self.rows else None
        def fetchall(self): return self.rows
    class Database:
        def execute_native(self, statement, parameters=()):
            statements.append(statement)
            if 'FROM repertoires' in statement: return Cursor([{'id': 'rep'}])
            if 'FROM opening_graph_publications' in statement: return Cursor([{'generation': 1, 'state': 'ready', 'task_generation': None, 'task_state': None}])
            if 'COUNT(*)' in statement: return Cursor([{'line_count': 1, 'source_bytes': 100}])
            if 'FROM repertoire_lines' in statement: return Cursor([{**source.lines[0].graph_line(), 'name': 'a'}])
            if 'FROM prefix_splits' in statement: return Cursor([])
            if 'SELECT DISTINCT step.card_id' in statement:
                return Cursor([{'id': card.id, 'start_fen': card.start_fen, 'moves_json': json.dumps(card.moves),
                    'trained_color': card.trained_color, 'revision': card.revision, 'archived': card.archived, 'linked': card.linked} for card in cards])
            if 'FROM opening_graph_steps' in statement:
                return Cursor([{**step.__dict__, 'moves_json': json.dumps(step.moves), 'decision_fen_keys_json': json.dumps(step.decision_fen_keys)} for step in source.published_steps])
            raise AssertionError(statement)
    @contextmanager
    def connection(**options):
        nonlocal open_read
        assert options == {'read_only': True, 'background': True, 'repeatable_read': True}
        open_read = True
        try: yield Database()
        finally: open_read = False
    monkeypatch.setattr(api.postgres_store, 'connection', connection)
    monkeypatch.setattr(api, 'check_available', lambda *_: None)
    loaded = api.load_snapshot('rep', 999999999)
    assert not open_read
    assert loaded.lines == source.lines and loaded.presentations == tuple(cards)
    assert all(statement.lstrip().startswith('SELECT') for statement in statements)
    assert loaded.published_steps == source.published_steps


def test_issue77_http_mid_calculation_preemption_returns_no_partial_metrics(prepared, monkeypatch):
    state, client = prepared
    checkpoints = []
    def stop_after_line(_deadline):
        checkpoints.append(1)
        if len(checkpoints) == 4:
            raise api.diagnostic_error('evaluation_busy', 'Foreground work arrived.', 503)
    monkeypatch.setattr(api, 'check_available', stop_after_line)
    result = client.post('/api/repertoires/rep/prefix-evaluation/evaluate', json={
        'snapshot_id': snapshot_identity(state['source']), 'selected_line_ids': ['a'], 'candidate_depths': {'a': 2}})
    assert result.status_code == 503 and result.headers['retry-after'] == '1'
    assert result.json()['detail']['code'] == 'evaluation_busy' and 'selected' not in result.json()


def test_issue77_runtime_guard_classifies_only_diagnostics_as_background_query_only(monkeypatch):
    from app import main, database
    from starlette.requests import Request
    from starlette.responses import Response
    monkeypatch.setattr(api.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(api.redis_admission_gate, 'configured', lambda: False)
    observed = []
    async def probe(method, path):
        async def downstream(_request):
            observed.append((api.activity_gate.in_background, database._query_only_request.get()))
            return Response(status_code=204)
        request = Request({'type': 'http', 'method': method, 'path': path, 'headers': [], 'query_string': b''})
        return await main.prioritize_foreground_requests(request, downstream)
    for method, suffix in [('GET', 'source'), ('POST', 'evaluate')]:
        assert asyncio.run(probe(method, '/api/repertoires/rep/prefix-evaluation/' + suffix)).status_code == 204
    assert observed == [(True, True), (True, True)]
    assert asyncio.run(probe('GET', '/api/queue/today')).status_code == 204
    assert observed[-1] == (False, True)


@pytest.mark.parametrize('authoritative', [False, True])
def test_issue77_repeatable_reader_and_worker_connections_set_isolation_before_budgets(monkeypatch, authoritative):
    statements = []
    class Raw:
        def execute(self, statement, *_args): statements.append(statement)
    raw = Raw()
    class Pool:
        @contextmanager
        def connection(self): yield raw
    selected_pools = []
    def select_pool(read_only):
        selected_pools.append(read_only)
        return Pool()
    monkeypatch.setattr(api.postgres_store, '_pool', select_pool)
    with api.postgres_store.connection(read_only=True, authoritative=authoritative, background=True, repeatable_read=True):
        pass
    assert selected_pools == [not authoritative]
    assert statements[:2] == ['SET TRANSACTION ISOLATION LEVEL REPEATABLE READ', 'SET TRANSACTION READ ONLY']
    assert all('set_config' in statement for statement in statements[2:])
