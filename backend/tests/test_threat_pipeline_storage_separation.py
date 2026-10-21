"""Defensive-threat application choices retain original fixed-storage boundaries."""
import ast
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import chess
import pytest

from app.services import threat_pipeline as pipeline

TASK = dict(id='task', generation=2, lease_token='lease', payload={})


def scopes(monkeypatch, events):
    database = object()
    def section(name):
        @contextmanager
        def scope(**options):
            events.append((name, 'open'))
            try:
                yield database
            finally:
                events.append((name, 'closed'))
        return scope
    monkeypatch.setattr(pipeline, 'background_read_connection', section('read'))
    monkeypatch.setattr(pipeline, 'read_connection', section('read'))
    monkeypatch.setattr(pipeline, 'connection', section('write'))
    monkeypatch.setattr(pipeline.activity_gate, 'wait_for_foreground', lambda: events.append(('yield',)))
    monkeypatch.setattr(pipeline, '_now', lambda: events.append(('clock',)) or 'now')
    return database


@pytest.mark.parametrize('still_same', [False, True])
def test_report_audit_checks_after_read_close_and_repairs_only_same_saved_evidence(monkeypatch, still_same):
    events = []
    database = scopes(monkeypatch, events)
    monkeypatch.setattr(pipeline.postgres_store, 'configured', lambda: False)
    item = dict(id='request', request_json='{}', report_json='{}')
    monkeypatch.setattr(pipeline.storage, 'next_saved_report', lambda *_: item)
    monkeypatch.setattr(pipeline, '_request_from_json', lambda _: 'request')
    monkeypatch.setattr(pipeline, 'report_from_json', lambda _: 'report')
    def reject(*_):
        events.append(('validate',))
        assert ('read', 'closed') in events and ('write', 'open') not in events
        raise ValueError('invalid report')
    monkeypatch.setattr(pipeline, 'validate_analysis_report', reject)
    monkeypatch.setattr(pipeline.storage, 'task_lease', lambda *_: dict(generation=2, lease_token='lease'))
    monkeypatch.setattr(pipeline.storage, 'saved_report',
                        lambda *_: dict(state='complete', report_json='{}' if still_same else 'changed'))
    for operation in ('archive_report', 'queue_report_repair', 'reset_linked_candidates', 'block_linked_cards', 'advance_audit'):
        monkeypatch.setattr(pipeline.storage, operation,
                            lambda *args, operation=operation: events.append((operation, *args)))
    assert pipeline.execute_threat_report_audit(TASK)
    effects = [event[0] for event in events if event[0] in {
        'archive_report', 'queue_report_repair', 'reset_linked_candidates', 'block_linked_cards', 'advance_audit'}]
    assert effects == (['archive_report', 'queue_report_repair', 'reset_linked_candidates', 'block_linked_cards']
                       if still_same else []) + ['advance_audit']
    assert events.count(('clock',)) == (5 if still_same else 2)
    assert all(event[1] is database for event in events if event[0] in effects)


def test_empty_report_audit_closes_read_without_validation_or_writer(monkeypatch):
    events = []
    scopes(monkeypatch, events)
    monkeypatch.setattr(pipeline.storage, 'next_saved_report', lambda *_: None)
    assert pipeline.execute_threat_report_audit(TASK) is False
    assert events == [('yield',), ('read', 'open'), ('read', 'closed')]


@pytest.mark.parametrize('current_lease', [False, True])
def test_sqlite_backfill_checks_lease_then_advances_empty_games_to_repertoires(monkeypatch, current_lease):
    events = []
    scopes(monkeypatch, events)
    monkeypatch.setattr(pipeline.postgres_store, 'configured', lambda: False)
    monkeypatch.setattr(pipeline.storage, 'next_analyzed_game', lambda *_: None)
    monkeypatch.setattr(pipeline.storage, 'task_lease',
                        lambda *_: dict(generation=2 if current_lease else 1, lease_token='lease'))
    monkeypatch.setattr(pipeline.storage, 'advance_backfill', lambda *args: events.append(('advance', *args)))
    assert pipeline.execute_threat_backfill_slice(TASK)
    advanced = [event for event in events if event[0] == 'advance']
    assert len(advanced) == int(current_lease)
    if current_lease:
        assert advanced[0][2:] == ('{"phase": "repertoires", "cursor": ""}', 'now', 'now', 'task', 2, 'lease')


def test_scan_prepares_one_seed_after_read_close_then_rechecks_source_before_publication(monkeypatch):
    events = []
    scopes(monkeypatch, events)
    monkeypatch.setattr(pipeline.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(pipeline.storage, 'scan_game', lambda *_: dict(id='game', analysis_version=1,
                        start_fen=chess.STARTING_FEN, moves_json='["e2e4"]', color='white'))
    monkeypatch.setattr(pipeline.storage, 'scan_candidate_lines', lambda *_: [])
    monkeypatch.setattr(pipeline, 'find_defensive_knight_forks', lambda *_: ['first', 'second'])
    def prepare(_game, seed):
        assert events[-1] == ('read', 'closed')
        events.append(('prepare', seed))
        return 'prepared'
    monkeypatch.setattr(pipeline, '_prepare_seed', prepare)
    monkeypatch.setattr(pipeline, 'lock_current_slice', lambda *_: True)
    monkeypatch.setattr(pipeline.storage, 'current_game_version', lambda *_: (2,))
    monkeypatch.setattr(pipeline, '_upsert_seed', lambda *_args, **_kwargs: pytest.fail('Stale source was published'))
    task = {**TASK, 'payload': dict(game_id='game', analysis_version=1, cursor=0)}
    assert pipeline.execute_threat_scan_slice(task) is False
    assert ('prepare', 'first') in events and ('prepare', 'second') not in events
    assert events.index(('prepare', 'first')) < events.index(('write', 'open'))


@pytest.mark.parametrize('interactive', [False, True])
def test_claim_retains_filter_expiry_query_and_fairness_before_lease(monkeypatch, interactive):
    from app.services import engine_scheduling
    events = []
    scopes(monkeypatch, events)
    monkeypatch.setattr(pipeline.storage, 'begin_claim', lambda *_: events.append(('begin',)))
    monkeypatch.setattr(pipeline, 'analysis_enabled', lambda *_: events.append(('enabled',)) or False)
    def selector(enabled):
        events.append(('filter', enabled))
        return lambda _: events.append(('select',)) or dict(id='request', request_json='{}')
    monkeypatch.setattr(pipeline.storage, 'request_selector', selector)
    monkeypatch.setattr(pipeline.storage, 'requeue_expired_requests', lambda *_: events.append(('expiry',)))
    monkeypatch.setattr(pipeline.storage, 'interactive_request', lambda *_: (1,) if interactive else None)
    monkeypatch.setattr(engine_scheduling, 'lock_engine_scheduling_state', lambda *_: events.append(('fairness',)) or 1)
    monkeypatch.setattr(engine_scheduling, 'reserved_ordinary_game', lambda *_: events.append(('reserved',)) or True)
    monkeypatch.setattr(pipeline.uuid, 'uuid4', lambda: events.append(('uuid',)) or 'uuid')
    monkeypatch.setattr(pipeline, 'datetime', SimpleNamespace(now=lambda *_: datetime(2026, 10, 21, tzinfo=timezone.utc)))
    monkeypatch.setattr(pipeline.storage, 'claim_request', lambda *_: events.append(('claim',)) or 1)
    result = pipeline.claim_analysis_request()
    assert [event[0] for event in events if event[0] in {'begin', 'enabled', 'filter', 'expiry', 'select'}] == [
        'begin', 'enabled', 'filter', 'expiry', 'select']
    assert ('uuid',) in events if interactive else ('uuid',) not in events
    assert result == (dict(id='request', request={}, lease_id='uuid') if interactive else None)


@pytest.mark.parametrize('state,lease,stored_request,expected', [
    ('complete', 'lease', '{}', ()),
    ('leased', 'stale', '{}', 'Analysis lease is stale'),
    ('leased', 'lease', 'changed', 'Analysis request changed'),
    ('leased', 'lease', '{}', ('candidate',)),
])
def test_saved_report_checks_after_read_close_and_fences_actual_writer_state(monkeypatch, state, lease, stored_request, expected):
    events = []
    scopes(monkeypatch, events)
    monkeypatch.setattr(pipeline, 'report_from_json', lambda _: SimpleNamespace(request=SimpleNamespace(request_id='request')))
    monkeypatch.setattr(pipeline, '_request_from_json', lambda _: 'typed')
    def validate(*_):
        assert events[-1] == ('read', 'closed')
        events.append(('validate',))
    monkeypatch.setattr(pipeline, 'validate_analysis_report', validate)
    monkeypatch.setattr(pipeline.storage, 'saved_request_json', lambda *_: dict(request_json='{}'))
    monkeypatch.setattr(pipeline.storage, 'saved_request_state',
                        lambda *_: dict(state=state, lease_id=lease, request_json=stored_request))
    monkeypatch.setattr(pipeline.storage, 'complete_report', lambda *_: events.append(('complete',)))
    monkeypatch.setattr(pipeline.storage, 'report_candidate_ids', lambda *_: [('candidate',)])
    if isinstance(expected, str):
        with pytest.raises(ValueError, match=expected):
            pipeline.save_analysis_report('request', 'lease', {})
    else:
        assert pipeline.save_analysis_report('request', 'lease', {}) == expected
    assert events.count(('complete',)) == int(expected == ('candidate',))
    assert events.count(('clock',)) == int(expected == ('candidate',))


def test_threat_pipeline_contains_no_sql_driver_calls_and_storage_no_application_imports():
    tree = ast.parse(Path(pipeline.__file__).read_text())
    forbidden = {'execute', 'execute_native', 'cursor', 'fetchone', 'fetchall', 'fetchmany', 'rowcount', 'commit', 'rollback'}
    assert not any(isinstance(node, ast.Attribute) and node.attr in forbidden for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Constant) and isinstance(node.value, str)
                   and any(word in node.value.upper() for word in ('SELECT ', 'INSERT INTO ', 'UPDATE ', 'DELETE FROM ', 'BEGIN IMMEDIATE'))
                   for node in ast.walk(tree))
    for node in ast.walk(ast.parse(Path(pipeline.storage.__file__).read_text())):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or '').startswith('app')
        elif isinstance(node, ast.Import):
            assert all(not name.name.startswith('app') for name in node.names)
    assert pipeline.THREAT_CANDIDATE_UPSERT_SQL is pipeline.storage.THREAT_CANDIDATE_UPSERT_SQL
