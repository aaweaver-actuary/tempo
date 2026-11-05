"""The real worker selects the retained Games sync command only on dispatch."""

import os
import subprocess
import sys


def run_fresh(script):
    guard = "import socket\ndef no_network(*args, **kwargs): raise AssertionError('unexpected network')\nsocket.socket.connect = no_network\nsocket.create_connection = no_network\n"
    subprocess.run(
        [sys.executable, "-c", guard + script],
        env=os.environ.copy(),
        check=True,
        timeout=20,
    )


def test_worker_cold_import_does_not_load_games_sync_command():
    run_fresh("""
import importlib.abc, sys
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'app.game_sync_commands': raise AssertionError(fullname)
sys.meta_path.insert(0, Deny())
from app import tasks, command_gateway
assert 'app.game_sync_commands' not in sys.modules
assert not command_gateway.is_command_registered('games.sync.enqueue')
assert command_gateway.is_command_registered('queue.issue_head')
""")


def test_actual_worker_resolves_and_dispatches_original_retained_command():
    run_fresh("""
from contextlib import nullcontext
from app import tasks, command_gateway
from app.services import background_runtime
assert not command_gateway.is_command_registered('games.sync.enqueue')
payload = {'lichess_username': ' alice '}
seen = []
tasks.activity_gate.foreground = lambda: nullcontext()
tasks._preparation_capacity = lambda *_: nullcontext(True)
background_runtime.measure_training_timing = lambda *_: nullcontext(None)
def attempt(operation_id, name, original, **kw):
    assert not command_gateway.is_command_registered(name)
    seen.append(('receipt', operation_id, name, original, kw))
    return True, original, 'original-token', 1
tasks.record_operation_attempt = attempt
def execute(operation_id, name, original, **kw):
    from app import game_sync_commands as commands
    assert command_gateway._handlers[name] is commands.enqueue_game_sync
    seen.append(('execute', operation_id, name, original, kw))
    commands.storage.lock_sync_admission = lambda db: None
    commands.storage.active_sync_job = lambda db: {'id': 'existing', 'status': 'queued'}
    commands.storage.existing_job_window = lambda db, job_id: True
    return commands.enqueue_game_sync(object(), original)
tasks.execute_command = execute
result = tasks.execute_foreground_command.run('retained-id', 'games.sync.enqueue', payload)
assert result['job_id'] == 'existing'
assert seen[0][1:4] == ('retained-id', 'games.sync.enqueue', payload)
assert seen[1][1:4] == ('retained-id', 'games.sync.enqueue', payload)
assert seen[1][4] == {'attempt_token': 'original-token'}
""")


def test_concurrent_resolution_and_failed_import_retry():
    run_fresh("""
import builtins, concurrent.futures
from app import command_gateway
from app.product_composition import worker_game_sync as owner
original = builtins.__import__
failures = []
def intercept(name, *args, **kwargs):
    if name == 'app' and 'game_sync_commands' in (args[2] if len(args) > 2 else ()) and not failures:
        failures.append(True); raise ImportError('synthetic optional import failure')
    return original(name, *args, **kwargs)
builtins.__import__ = intercept
try:
    owner.resolve_worker_game_sync_command('games.sync.enqueue')
except ImportError:
    pass
else:
    raise AssertionError('failure was hidden')
assert not command_gateway.is_command_registered('games.sync.enqueue')
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    list(pool.map(owner.resolve_worker_game_sync_command, ['games.sync.enqueue'] * 2))
from app import game_sync_commands
assert command_gateway.is_command_registered('games.sync.enqueue')
assert command_gateway._handlers['games.sync.enqueue'] is game_sync_commands.enqueue_game_sync
""")


def test_study_dispatch_does_not_wait_for_blocked_optional_resolution():
    run_fresh("""
import builtins, threading
from contextlib import nullcontext
from app import tasks
from app.services import background_runtime
started, release, done = threading.Event(), threading.Event(), threading.Event()
original = builtins.__import__
def intercept(name, *args, **kwargs):
    if name == 'app' and 'game_sync_commands' in (args[2] if len(args) > 2 else ()):
        started.set(); assert release.wait(5)
    return original(name, *args, **kwargs)
builtins.__import__ = intercept
tasks.activity_gate.foreground = lambda: nullcontext()
tasks._preparation_capacity = lambda *_: nullcontext(True)
background_runtime.measure_training_timing = lambda *_: nullcontext(None)
tasks.record_operation_attempt = lambda *a, **k: (True, a[2], 'token', 1)
tasks.execute_command = lambda operation_id, name, payload, **kw: {'command': name}
optional = threading.Thread(target=lambda: tasks.execute_foreground_command.run('optional', 'games.sync.enqueue', {}))
optional.start(); assert started.wait(5)
def study():
    assert tasks.execute_foreground_command.run('study', 'queue.issue_head', {}) == {'command': 'queue.issue_head'}
    done.set()
worker = threading.Thread(target=study); worker.start()
try:
    assert done.wait(3), 'Study waited for optional registration'
finally:
    release.set(); optional.join(5); worker.join(5)
assert not optional.is_alive() and not worker.is_alive()
""")


def test_failed_import_follows_receipt_and_records_original_fenced_failure():
    run_fresh("""
from contextlib import nullcontext
from app import tasks, command_gateway
from app.services import background_runtime
payload = {'lichess_username': 'alice'}
events = []
tasks.activity_gate.foreground = lambda: nullcontext()
tasks._preparation_capacity = lambda *_: nullcontext(True)
background_runtime.measure_training_timing = lambda *_: nullcontext(None)
def attempt(operation_id, name, original, **kw):
    events.append(('receipt', operation_id, name, original, kw))
    return True, original, 'fenced-token', 1
tasks.record_operation_attempt = attempt
error = ImportError('synthetic missing sync dependency')
def unavailable(name):
    assert events[0][0] == 'receipt'
    events.append(('resolve', name))
    raise error
tasks.resolve_worker_game_sync_command = unavailable
tasks.execute_command = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('unexpected publication'))
def retry(operation_id, token, exc, **kw):
    events.append(('failure', operation_id, token, exc, kw))
tasks.record_operation_retry = retry
try:
    tasks.execute_foreground_command.run('retained-id', 'games.sync.enqueue', payload)
except ImportError as caught:
    assert caught is error
else: raise AssertionError('import failure was hidden')
assert events[0][1:4] == ('retained-id', 'games.sync.enqueue', payload)
assert events[1] == ('resolve', 'games.sync.enqueue')
assert events[2] == ('failure', 'retained-id', 'fenced-token', error, {'retryable': False, 'background': False})
assert not command_gateway.is_command_registered('games.sync.enqueue')
""")


def test_finished_receipt_does_not_import_optional_command():
    run_fresh("""
from contextlib import nullcontext
from app import tasks, command_gateway
from app.services import background_runtime
tasks.activity_gate.foreground = lambda: nullcontext()
tasks._preparation_capacity = lambda *_: nullcontext(True)
background_runtime.measure_training_timing = lambda *_: nullcontext(None)
tasks.record_operation_attempt = lambda *_a, **_k: (False, {}, None, 1)
tasks.resolve_worker_game_sync_command = lambda *_: (_ for _ in ()).throw(AssertionError('unexpected import'))
assert tasks.execute_foreground_command.run('completed', 'games.sync.enqueue', {}) is None
def conflict(*_a, **_k): raise command_gateway.CommandConflict('changed payload')
tasks.record_operation_attempt = conflict
try:
    tasks.execute_foreground_command.run('conflict', 'games.sync.enqueue', {})
except command_gateway.CommandConflict:
    pass
else: raise AssertionError('conflict was hidden')
assert not command_gateway.is_command_registered('games.sync.enqueue')
""")


def test_retained_queued_delivery_resolves_after_dependency_recovers():
    run_fresh("""
from contextlib import nullcontext
from app import tasks, command_gateway
from app.services import background_runtime
payload = {'lichess_username': 'alice'}
tasks.activity_gate.foreground = lambda: nullcontext()
tasks._preparation_capacity = lambda *_: nullcontext(True)
background_runtime.measure_training_timing = lambda *_: nullcontext(None)
tasks.record_operation_attempt = lambda op, name, original, **kw: (True, original, 'recovered-token', 2)
def execute(op, name, original, **kw):
    from app import game_sync_commands
    assert command_gateway._handlers[name] is game_sync_commands.enqueue_game_sync
    assert (op, original, kw['attempt_token']) == ('queued-id', payload, 'recovered-token')
    return {'accepted': True}
tasks.execute_command = execute
assert tasks.execute_foreground_command.run('queued-id', 'games.sync.enqueue', payload) == {'accepted': True}
""")
