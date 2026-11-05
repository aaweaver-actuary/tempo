"""Foreground deferral is public without importing the prefix HTTP API."""

import os
from pathlib import Path
import subprocess
import sys

from app import prefix_evaluation_api
from app.contracts.prefix_errors import PrefixEvaluationForegroundDeferred


def test_public_foreground_defer_alias_and_http_contract():
    assert (
        prefix_evaluation_api.PrefixEvaluationForegroundDeferred
        is PrefixEvaluationForegroundDeferred
    )
    error = PrefixEvaluationForegroundDeferred()
    assert error.status_code == 503
    assert error.detail == {
        "code": "evaluation_busy",
        "message": "Study work is active. Retry the diagnostic when study is idle.",
    }
    assert error.headers == {"Retry-After": "1"}


def test_gateway_defer_does_not_import_prefix_http_api_or_open_writer():
    code = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.endswith('prefix_evaluation_api'):
        raise AssertionError('HTTP API imported')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from app import command_gateway as gateway
from app.contracts.prefix_errors import PrefixEvaluationForegroundDeferred
name = 'test.defer'
gateway._handlers[name] = lambda *_: (_ for _ in ()).throw(AssertionError('handler called'))
def prepare(_):
    raise PrefixEvaluationForegroundDeferred()
gateway._preparers[name] = prepare
gateway._writer_connection = lambda *_: (_ for _ in ()).throw(AssertionError('writer opened'))
try:
    gateway.execute_command('operation', name, {})
except gateway.BackgroundAdmissionDeferred:
    pass
else:
    raise AssertionError('expected foreground deferral')
"""
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=True,
    )
