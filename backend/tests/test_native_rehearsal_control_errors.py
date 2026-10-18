"""Native wait helpers must distinguish unavailable control from contention."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import HTTPException
import psycopg
import pytest

from app.services import redis_admission_gate


@pytest.mark.parametrize('filename,function_name', [
    ('check_postgres_opening_segmentation.py', 'admitted_segmentation_call'),
    ('check_postgres_opening_evidence.py', '_wait_for_checkpoint_admission'),
    ('check_postgres_prefix_transition_application.py', 'idle_call'),
])
@pytest.mark.parametrize('with_cause', [False, True])
def test_native_wait_helpers_preserve_unavailable_control_without_retry(monkeypatch, filename, function_name, with_cause):
    path = Path(__file__).resolve().parents[2] / 'scripts' / filename
    function = next(node for node in ast.parse(path.read_text()).body
                    if isinstance(node, ast.FunctionDef) and node.name == function_name)
    clock = SimpleNamespace(monotonic=Mock(side_effect=[0, 1, 11]),
                            sleep=Mock(side_effect=AssertionError('Control outage was retried')))
    namespace = {'time': clock, 'HTTPException': HTTPException, 'psycopg': psycopg,
                 'BackgroundAdmissionDeferred': redis_admission_gate.BackgroundAdmissionDeferred}
    # Compile the actual helper body without starting its unrelated native fixture.
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
    monkeypatch.setattr(redis_admission_gate, 'foreground_present', lambda: False)
    failure = redis_admission_gate.BackgroundAdmissionDeferred(
        'Waiting for foreground activity' if with_cause else 'Foreground admission is unavailable')
    if with_cause:
        failure.__cause__ = ConnectionError('Control connection failed')
    operation = Mock(side_effect=failure)
    with pytest.raises(redis_admission_gate.BackgroundAdmissionDeferred) as raised:
        namespace[function_name](operation)
    assert raised.value is failure
    operation.assert_called_once_with()
    clock.sleep.assert_not_called()
