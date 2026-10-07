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
        def execute_native(self, query, parameters):
            assert active_read
            queries.append(query)
            name = re.search(r'FROM (\w+)', query).group(1)
            rows = fixture.rows(name)
            if query.startswith('SELECT COUNT(*)'):
                return Cursor({'count': api.MAX_TRANSITION_ROWS + 1 if oversized == 'rows' else len(rows),
                               'bytes': api.MAX_TRANSITION_BYTES + 1 if oversized == 'bytes' else len(canonical_json(rows))})
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
        assert all(query.startswith('SELECT ') for query in queries)


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
