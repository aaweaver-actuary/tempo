"""Changed receipt/worker boundaries; fake storage is not native PostgreSQL proof."""
from contextlib import nullcontext
import json
from types import SimpleNamespace

import pytest

from app import tasks
from app.services import durable_tasks, explicit_repertoire_import as preparation, study_mode
from app.services.redis_admission_gate import BackgroundAdmissionDeferred
from tempo_legacy_database.adapters.authored_repertoires.pgn_import import preparation as storage


def delivery(kind='opening_graph_rebuild'):
    return dict(id='graph', kind=kind, generation=2, lease_token='lease', phase='stage',
                payload={'origin_import_operation_id': 'import'})


@pytest.mark.parametrize('kind', ['opening_graph_rebuild', 'integrity_scan'])
def test_delivery_requires_exact_current_row_body_and_phase(monkeypatch, kind):
    task = delivery(kind)
    row = dict(kind=kind, phase='stage', payload_json=json.dumps(task['payload']))
    calls = []
    monkeypatch.setattr(storage, 'delivery', lambda db, *fence: calls.append(fence) or row)
    assert preparation.permitted_delivery(task, object())
    assert calls == [('graph', 2, 'lease')]
    row['phase'] = 'finalize'
    assert not preparation.permitted_delivery(task, object())
    row['phase'] = 'stage'
    row['payload_json'] = '{}'
    assert not preparation.permitted_delivery(task, object())


@pytest.mark.parametrize('task', [delivery('opening_segmentation'), delivery('game_analysis_publish'),
    {**delivery(), 'payload': {}}, {**delivery(), 'payload': {'origin_import_operation_id': ''}}])
def test_unrelated_or_old_delivery_is_denied_without_opening_storage(monkeypatch, task):
    monkeypatch.setattr(preparation, 'background_read_connection', lambda **k: pytest.fail('Unrelated work opened storage'))
    assert not preparation.permitted_delivery(task)




@pytest.mark.parametrize('permitted', [False, True])
def test_worker_rechecks_scope_before_handler_and_keeps_foreground_gate(monkeypatch, permitted):
    calls = []
    monkeypatch.setattr(tasks, 'measure_handler', lambda *a: nullcontext())
    monkeypatch.setattr(tasks, 'study_only_enabled', lambda: True)
    monkeypatch.setattr(tasks.activity_gate, 'check_background_admission', lambda: calls.append('foreground'))
    monkeypatch.setattr(preparation, 'permitted_delivery', lambda task: calls.append('scope') or permitted)
    monkeypatch.setattr(tasks, 'defer_task_for_foreground', lambda task: calls.append('defer'))
    monkeypatch.setattr(tasks, 'defer_paused_defensive_task', lambda task: False)
    monkeypatch.setattr(tasks, 'current_delivery', lambda task: True)
    def handler(task):
        assert task['explicit_import_preparation'] is True
        calls.append('handler')
        return False
    monkeypatch.setattr(tasks, 'execute_postgres_opening_graph_slice', handler)
    assert tasks._execute_claimed_background_slice(delivery(), None) is False
    assert calls == ['foreground', 'scope', 'handler' if permitted else 'defer']
    assert study_mode.STUDY_TASK_KINDS == ('daily_queue',)
    assert 'imports.pgn.admit' in study_mode.PAUSED_IMPORT_COMMANDS
    assert preparation.COMMAND not in study_mode.PAUSED_IMPORT_COMMANDS


def test_owner_transaction_rechecks_receipt_after_lease_lock(monkeypatch):
    task = {**delivery(), 'explicit_import_preparation': True}
    monkeypatch.setattr(durable_tasks.storage, 'lock_slice', lambda db, args: {**task, 'state': 'leased'})
    monkeypatch.setattr(preparation, 'permitted_delivery', lambda task, database=None: False)
    with pytest.raises(BackgroundAdmissionDeferred):
        durable_tasks.lock_current_slice(object(), task)


@pytest.mark.parametrize('command', ['imports.pgn.explicit', 'imports.pgn.admit', 'studies.import.commit'])
def test_only_deliberate_new_import_uses_foreground_admission_in_study_only(monkeypatch, command):
    calls = []
    monkeypatch.setattr(tasks.activity_gate, 'foreground', lambda: nullcontext())
    monkeypatch.setattr(tasks, 'study_only_enabled', lambda: True)
    monkeypatch.setattr(tasks, 'record_operation_attempt', lambda *a, **k: (True, {'saved': 'payload'}, 'token', 1))
    monkeypatch.setattr(tasks, 'defer_operation_for_foreground', lambda *a: calls.append('deferred'))
    def execute(operation_id, command_name, payload, **kwargs):
        assert (operation_id, command_name, payload, kwargs) == ('import', command, {'saved': 'payload'}, {'attempt_token': 'token'})
        calls.append('executed')
        return 'saved'
    monkeypatch.setattr(tasks, 'execute_command', execute)
    result = tasks._execute_foreground_command(SimpleNamespace(request=SimpleNamespace(headers={})), 'import', command, {})
    assert calls == (['executed'] if command == preparation.COMMAND else ['deferred'])
    assert result == ('saved' if command == preparation.COMMAND else None)


@pytest.mark.parametrize('essential', [None, {'id': 'queue'}])
def test_daily_queue_has_first_claim_and_import_fallback_is_one_slice(monkeypatch, essential):
    calls = []
    monkeypatch.setattr(tasks, 'study_only_enabled', lambda: True)
    monkeypatch.setattr(tasks.activity_gate, 'check_background_admission', lambda: None)
    def claim(**kwargs):
        calls.append(kwargs)
        return essential if len(calls) == 1 else delivery()
    monkeypatch.setattr(tasks, 'claim_task', claim)
    monkeypatch.setattr(tasks, '_execute_claimed_background_slice', lambda *a, **k: False)
    # The maintenance decorator is an unchanged operational control.
    function = tasks.poll_background_tasks.run
    while hasattr(function, '__wrapped__'):
        function = function.__wrapped__
    function(SimpleNamespace(request=SimpleNamespace(headers={})))
    assert calls[0] == {'allowed_kinds': ('daily_queue',)}
    assert calls[1:] == ([{'explicit_imports_only': True}] if essential is None else [])
