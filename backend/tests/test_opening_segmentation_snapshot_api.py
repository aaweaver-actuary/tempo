"""Snapshot bindings reject obsolete pagination and preferences before exposing content."""
from contextlib import contextmanager
import json

import pytest
from fastapi import HTTPException

from app import opening_segmentation_api as api


@pytest.fixture
def prepared(monkeypatch):
    state = dict(state='ready', run_id='run-one', content_version=3, graph_generation=2, error=None)
    recommendation = dict(run_id='run-one', id='rec', repertoire_id='rep', kind='shared_trunk',
                          source_fingerprint='sources', decisions_before=48, decisions_after=20,
                          decisions_avoided=28, additional_starts=1, segment_count=9)
    statements = []
    class Row(dict):
        def __getitem__(self, key): return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)
    class Cursor:
        def __init__(self, rows): self.rows = rows
        def fetchone(self): return self.rows[0] if self.rows else None
        def fetchall(self): return self.rows
    class Database:
        def execute_native(self, statement, arguments=()):
            statements.append((statement, arguments))
            if statement.startswith('SELECT recommendation.') or statement.startswith('SELECT * FROM opening_segmentation_recommendations'):
                return Cursor([recommendation])
            if 'SELECT source_fingerprint' in statement: return Cursor([('sources',)])
            if 'SELECT COUNT' in statement: return Cursor([(0,)])
            if 'SELECT segment_id' in statement: return Cursor([(f'segment-{index}', json.dumps({'id': f'segment-{index}'})) for index in range(9)])
            if 'SELECT DISTINCT line' in statement: return Cursor([Row(id=f'route-{index}', name=f'Route {index}') for index in range(9)])
            return Cursor([])
    database = Database()
    @contextmanager
    def connection(): yield database
    monkeypatch.setattr(api, 'read_connection', connection)
    monkeypatch.setattr(api, 'require_postgres', lambda: None)
    monkeypatch.setattr(api, 'state_projection', lambda *_: dict(state))
    return state, recommendation, database, statements


def test_normal_pagination_reads_one_consistent_preview_snapshot(prepared):
    state, recommendation, _, statements = prepared
    listing = api.segmentation_list('rep')
    snapshot = listing['recommendations'][0]['snapshot_id']
    statements.clear()
    detail = api.segmentation_detail('rep', 'rec', after_segment='segment-7', snapshot_id=snapshot)
    assert detail['snapshot_id'] == detail['recommendation']['snapshot_id'] == snapshot
    assert statements[0][0] == 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'
    assert len(detail['segments']) == len(detail['routes']) == 8
    assert detail['next_segment'] == 'segment-7' and detail['next_route'] == 'route-7'
    assert all('LIMIT 9' in sql for sql, _ in statements if 'SELECT segment_id' in sql or 'SELECT DISTINCT' in sql)


@pytest.mark.parametrize('cursor', ['after_segment', 'after_route'])
@pytest.mark.parametrize('cursor_value', ['', 'last'])
def test_pagination_requires_snapshot_and_rejects_republication(prepared, cursor, cursor_value):
    state, _, _, statements = prepared
    snapshot = api.segmentation_list('rep')['recommendations'][0]['snapshot_id']
    statements.clear()
    with pytest.raises(HTTPException) as missing:
        api.segmentation_detail('rep', 'rec', **{cursor: cursor_value})
    assert missing.value.status_code == 409 and not statements
    state['run_id'] = 'run-two'
    with pytest.raises(HTTPException) as changed:
        api.segmentation_detail('rep', 'rec', snapshot_id=snapshot, **{cursor: cursor_value})
    assert changed.value.status_code == 409
    assert not any('SELECT segment_id' in sql or 'SELECT DISTINCT' in sql for sql, _ in statements)


@pytest.mark.parametrize('field', ['run_id', 'content_version', 'graph_generation', 'id', 'repertoire_id', 'source_fingerprint', 'policy_version', 'contract_version'])
def test_snapshot_identity_covers_every_publication_component(prepared, monkeypatch, field):
    state, recommendation, _, _ = prepared
    initial = api.recommendation_snapshot(state, recommendation)
    if field in state: state[field] = 4 if isinstance(state[field], int) else 'changed'
    elif field == 'policy_version': monkeypatch.setattr(api, 'POLICY_VERSION', 2)
    elif field == 'contract_version': monkeypatch.setattr(api, 'RECOMMENDATION_VERSION', 2)
    else: recommendation[field] = 'changed'
    assert api.recommendation_snapshot(state, recommendation) != initial


def test_preference_snapshot_mismatch_cannot_write_even_when_source_versions_match(prepared):
    _, _, database, statements = prepared
    with pytest.raises(HTTPException) as error:
        api.save_preference(database, {'repertoire_id': 'rep', 'recommendation_id': 'rec', 'request': {
            'choice': 'keep_current', 'content_version': 3, 'graph_generation': 2,
            'source_fingerprint': 'sources', 'snapshot_id': 'obsolete'}})
    assert error.value.status_code == 409
    assert not any(sql.startswith('INSERT') for sql, _ in statements)


def test_existing_preference_payload_retains_source_checks(prepared):
    _, _, database, _ = prepared
    assert api.save_preference(database, {'repertoire_id': 'rep', 'recommendation_id': 'rec', 'request': {
        'choice': 'keep_current', 'content_version': 3, 'graph_generation': 2, 'source_fingerprint': 'sources'}})['saved']

@pytest.mark.parametrize('snapshot', [None, 'snapshot-one'])
def test_preference_dispatch_preserves_legacy_receipt_payload_and_new_snapshot(monkeypatch, prepared, snapshot):
    from app import command_dispatch
    captured = []
    def dispatch(kind, payload, *, idempotency_key):
        captured.append((kind, payload, idempotency_key))
        return {'saved': True}
    monkeypatch.setattr(command_dispatch, 'dispatch_command', dispatch)
    original = {'choice': 'keep_current', 'content_version': 3, 'graph_generation': 2, 'source_fingerprint': 'sources'}
    if snapshot is not None: original['snapshot_id'] = snapshot
    api.request_preference('rep', 'rec', api.SegmentationPreference.model_validate(original), idempotency_key='retained-key')
    assert captured == [('opening.segmentation.preference', {
        'repertoire_id': 'rep', 'recommendation_id': 'rec', 'request': original}, 'retained-key')]
