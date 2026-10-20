"""Explicit v3 intake is study-compatible but still yields to foreground work."""
from contextlib import nullcontext

import pytest
from app import tasks
from app.services import study_mode
from app.services.redis_admission_gate import BackgroundAdmissionDeferred


@pytest.mark.parametrize('command', ['game_evidence.receive_legacy', 'games.analysis.start',
    'game_evidence.receive_legacy.batch', 'game_evidence.receive'])
def test_only_exact_intake_command_runs_in_study_only_mode(monkeypatch, command):
    calls = []
    monkeypatch.setattr(tasks, 'measure_handler', lambda *a: nullcontext())
    monkeypatch.setattr(tasks, 'study_only_enabled', lambda: True)
    def record(*args, **kwargs):
        assert tasks.activity_gate.in_background_control
        return True, {'delivery': 'saved immutable envelope'}, 'attempt-token', 1
    monkeypatch.setattr(tasks, 'record_operation_attempt', record)
    monkeypatch.setattr(tasks, 'defer_operation_for_foreground', lambda *a: calls.append(('deferred', a)))
    def admission():
        assert tasks.activity_gate.in_background and not tasks.activity_gate.in_background_control
        calls.append(('admission',))
    monkeypatch.setattr(tasks.activity_gate, 'check_background_admission', admission)
    def execute(*args, **kwargs):
        assert not tasks.activity_gate.in_background_control
        calls.append(('execute', args, kwargs))
        return 'accepted'
    monkeypatch.setattr(tasks, 'execute_command', execute)
    result = tasks.execute_background_command.run('explicit-operation', command, {'ignored': 'raw'})
    if command == 'game_evidence.receive_legacy':
        assert result == 'accepted' and [item[0] for item in calls] == ['admission', 'execute']
        assert calls[-1][1] == ('explicit-operation', command, {'delivery': 'saved immutable envelope'})
        assert calls[-1][2] == dict(background=True, attempt_token='attempt-token')
    else:
        assert result is None and calls == [('deferred', ('explicit-operation', 'attempt-token'))]
    assert study_mode.STUDY_TASK_KINDS == ('daily_queue',)
    assert 'game_evidence.receive_legacy' not in tasks._CONTROL_BACKGROUND_COMMANDS


def test_intake_foreground_race_is_durably_deferred_before_capture(monkeypatch):
    calls = []
    monkeypatch.setattr(tasks, 'measure_handler', lambda *a: nullcontext())
    monkeypatch.setattr(tasks, 'study_only_enabled', lambda: True)
    monkeypatch.setattr(tasks, 'record_operation_attempt', lambda *a, **k: (True, {}, 'token', 1))
    monkeypatch.setattr(tasks, 'execute_command', lambda *a, **k: pytest.fail('Capture/writer ran while foreground waited'))
    monkeypatch.setattr(tasks, 'defer_operation_for_foreground', lambda *a: calls.append(a))
    def denied():
        assert not tasks.activity_gate.in_background_control
        raise BackgroundAdmissionDeferred('Waiting for foreground Study')
    monkeypatch.setattr(tasks.activity_gate, 'check_background_admission', denied)
    assert tasks.execute_background_command.run('explicit-operation', 'game_evidence.receive_legacy', {}) is None
    assert calls == [('explicit-operation', 'token')]
