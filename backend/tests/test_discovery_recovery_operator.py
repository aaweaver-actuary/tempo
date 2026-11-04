"""Finite local operator transport for existing parked discovery identities."""
from contextlib import contextmanager
import json

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.product_composition import discovery_recovery_operator as operator
from tempo_legacy_database.adapters.commands import activity


@contextmanager
def reader():
    yield object()


def test_status_is_bounded_and_only_exposes_exact_parked_tuples(monkeypatch):
    rows = [dict(task_id=f'task-{i}', generation=1, intent_id=f'intent-{i}',
                 hold_reason='current_integrity_stale', identity_valid=True) for i in range(51)]
    rows[1]['identity_valid'] = False
    rows[2]['hold_reason'] = 'private payload with spaces'
    monkeypatch.setattr(activity, 'parked_discovery_tuples', lambda database, limit, after: rows[:limit])
    result = operator.list_parked(reader)
    assert result['truncated'] is True and result['next_after'] == 'task-49' and result['worker'] == 'disabled'
    assert len(result['parked']) == 48
    assert result['parked'][0] == dict(task_id='task-0', generation=1, intent_id='intent-0',
                                       hold_reason='current_integrity_stale', status='held')
    assert 'payload' not in json.dumps(result)


def test_retry_preserves_exact_operation_identity_and_reports_pending(monkeypatch, capsys):
    from app import command_dispatch
    from app.product_composition import database_infrastructure
    from tempo_legacy_database.adapters import postgres_store
    monkeypatch.setattr(database_infrastructure, 'bind_database_infrastructure', lambda: None)
    monkeypatch.setattr(postgres_store, 'configured', lambda: True)
    calls = []
    monkeypatch.setattr(command_dispatch, 'dispatch_command', lambda name, payload, **kw:
        calls.append((name, payload, kw)) or JSONResponse(status_code=202, content={'state': 'pending'}))
    args = ['retry', '--task-id', 'task', '--generation', '4', '--intent-id', 'intent',
            '--hold-reason', 'current_integrity_stale', '--operation-id', 'original-op']
    assert operator.main(args) == 0
    assert json.loads(capsys.readouterr().out) == {'status': 'pending', 'operation_id': 'original-op', 'worker': 'disabled'}
    assert calls == [('activity.task.retry', {'task_id': 'task', 'generation': 4, 'intent_id': 'intent',
                       'hold_reason': 'current_integrity_stale'}, {'idempotency_key': 'original-op'})]


def test_retry_does_not_expose_error_and_invalid_tuple_never_dispatches(monkeypatch, capsys):
    from app import command_dispatch
    from app.product_composition import database_infrastructure
    from tempo_legacy_database.adapters import postgres_store
    monkeypatch.setattr(database_infrastructure, 'bind_database_infrastructure', lambda: None)
    monkeypatch.setattr(postgres_store, 'configured', lambda: True)
    calls = []
    def held(*args, **kwargs):
        calls.append(args)
        raise HTTPException(409, 'private source data')
    monkeypatch.setattr(command_dispatch, 'dispatch_command', held)
    args = ['retry', '--task-id', 'task', '--generation', '1', '--intent-id', 'intent',
            '--hold-reason', 'current_integrity_stale', '--operation-id', 'original-op']
    assert operator.main(args) == 2
    assert json.loads(capsys.readouterr().out) == {'status': 'held', 'http_status': 409, 'operation_id': 'original-op', 'worker': 'disabled'}
    with pytest.raises(ValueError):
        operator.main(args[:-1] + ['bad id'])
    assert len(calls) == 1
