"""Retained legacy evidence commands select only their own worker registration."""
import os
import subprocess
import sys


def run_fresh(script):
    guard = '''
import socket
def no_network(*args, **kwargs): raise AssertionError('unexpected network')
socket.socket.connect = no_network
socket.create_connection = no_network
'''
    subprocess.run([sys.executable, '-c', guard + script], env=os.environ.copy(), check=True, timeout=20)


SETUP = '''
from contextlib import nullcontext
from app import tasks, command_gateway
tasks.activity_gate.foreground = lambda: nullcontext()
tasks.activity_gate.background_control = lambda: nullcontext()
tasks.activity_gate.background_job = lambda *_a, **_kw: nullcontext()
tasks.activity_gate.check_background_admission = lambda: None
tasks._preparation_capacity = lambda *_: nullcontext(True)
tasks.study_only_enabled = lambda: False
from app.services import background_runtime
background_runtime.measure_training_timing = lambda *_: nullcontext(None)
tasks.measure_handler = lambda *_a, **_kw: nullcontext()
'''


def test_cold_worker_and_essential_queue_do_not_import_legacy_evidence():
    run_fresh('''
import importlib.abc, sys
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'app.game_evidence_commands': raise AssertionError(fullname)
sys.meta_path.insert(0, Deny())
''' + SETUP + '''
tasks.activity_gate.essential_study_progress = lambda *_: nullcontext()
tasks.defer_paused_defensive_task = lambda _: False
tasks.current_delivery = lambda _: True
claimed = {'kind':'daily_queue','id':'study','generation':1,'lease_token':'lease','payload':{}}
tasks.claim_task = lambda kind: claimed
tasks.execute_postgres_queue_refresh_slice = lambda value: False if value is claimed else (_ for _ in ()).throw(AssertionError('changed task'))
assert tasks.progress_study_queue.run() is False
assert 'app.game_evidence_commands' not in sys.modules
''')


def test_retained_commands_register_only_selected_owner_after_attempt():
    run_fresh(SETUP + '''
from app.product_composition import worker_legacy_evidence as resolver
seen = []
def attempt(op, name, payload, **kw):
    seen.append(('attempt', op, name, payload, kw)); return True, payload, 'original-token', 1
tasks.record_operation_attempt = attempt
def execute(op, name, payload, **kw):
    from app import game_evidence_commands as owner
    assert command_gateway._handlers[name] is (owner.receive_legacy_delivery if name.endswith('receive_legacy') else owner.admit_legacy_card)
    if name.endswith('receive_legacy'): assert command_gateway._preparers[name] is owner.prepare_legacy_delivery
    seen.append(('execute', op, name, payload, kw)); return {'accepted':True}
tasks.execute_command = execute
receive = {'delivery': {'original':'envelope'}}
assert tasks.execute_background_command.run('receive-id','game_evidence.receive_legacy',receive) == {'accepted':True}
assert not command_gateway.is_command_registered('game_evidence.admit_legacy')
admit = {'card_id':'retained-card'}
assert tasks.execute_foreground_command.run('admit-id','game_evidence.admit_legacy',admit) == {'accepted':True}
assert [(x[0],x[1],x[2],x[3]) for x in seen] == [('attempt','receive-id','game_evidence.receive_legacy',receive),('execute','receive-id','game_evidence.receive_legacy',receive),('attempt','admit-id','game_evidence.admit_legacy',admit),('execute','admit-id','game_evidence.admit_legacy',admit)]
assert seen[1][4] == {'background':True,'attempt_token':'original-token'}
assert seen[3][4] == {'attempt_token':'original-token'}
''')


def test_failed_import_retries_selected_registration_once_without_blocking_study():
    run_fresh('''
import importlib.abc, sys, threading
from concurrent.futures import ThreadPoolExecutor
class DenyOnce(importlib.abc.MetaPathFinder):
    fail = True
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'app.game_evidence_commands' and self.fail:
            self.fail = False; raise ImportError('temporary unavailable')
sys.meta_path.insert(0, DenyOnce())
''' + SETUP + '''
from app.product_composition import worker_legacy_evidence as resolver
name = 'game_evidence.receive_legacy'
try: resolver.resolve_worker_legacy_evidence(name)
except ImportError: pass
else: raise AssertionError('missing import was hidden')
assert not command_gateway.is_command_registered(name) and name not in resolver._loaded
with ThreadPoolExecutor(max_workers=4) as pool:
    list(pool.map(resolver.resolve_worker_legacy_evidence, [name]*8))
assert command_gateway.is_command_registered(name)
assert not command_gateway.is_command_registered('game_evidence.admit_legacy')
# Another family never takes the evidence lock.
with resolver._lock:
    completed = threading.Event()
    worker = threading.Thread(target=lambda: (resolver.resolve_worker_legacy_evidence('queue.issue_head'), completed.set()))
    worker.start(); assert completed.wait(2)
worker.join(2); assert not worker.is_alive()
''')


def test_finished_conflict_and_import_failure_keep_original_fence():
    run_fresh(SETUP + '''
name = 'game_evidence.receive_legacy'
tasks.record_operation_attempt = lambda *_a, **_kw: (False, {}, None, 1)
tasks.resolve_worker_legacy_evidence = lambda *_: (_ for _ in ()).throw(AssertionError('unexpected resolve'))
assert tasks.execute_background_command.run('done', name, {}) is None
def conflict(*_a, **_kw): raise command_gateway.CommandConflict('changed')
tasks.record_operation_attempt = conflict
try: tasks.execute_background_command.run('conflict', name, {})
except command_gateway.CommandConflict: pass
else: raise AssertionError('conflict hidden')
tasks.record_operation_attempt = lambda *_a, **_kw: (True, {'original':1}, 'token', 1)
tasks.resolve_worker_legacy_evidence = lambda *_: (_ for _ in ()).throw(ImportError('unavailable'))
events = []
tasks.record_operation_retry = lambda op, token, error, **kw: events.append((op, token, str(error), kw))
try: tasks.execute_background_command.run('retained', name, {'original':1})
except ImportError: pass
else: raise AssertionError('failure hidden')
assert events == [('retained','token','unavailable',{'retryable':False,'background':True})]
''')


def test_factory_failure_does_not_register_either_command():
    run_fresh(SETUP + '''
from app import game_evidence_commands as owner
from app.product_composition import worker_legacy_evidence as resolver
name = 'game_evidence.admit_legacy'
original = owner.legacy_evidence_registration
owner.legacy_evidence_registration = lambda _: (_ for _ in ()).throw(ValueError('factory failed'))
try: resolver.resolve_worker_legacy_evidence(name)
except ValueError: pass
else: raise AssertionError('factory failure hidden')
assert not command_gateway.is_command_registered(name) and name not in resolver._loaded
assert not command_gateway.is_command_registered('game_evidence.receive_legacy')
owner.legacy_evidence_registration = original
resolver.resolve_worker_legacy_evidence(name)
assert command_gateway._handlers[name] is owner.admit_legacy_card
''')


def test_preexisting_registration_conflicts_without_replacing_handler_or_fence():
    run_fresh(SETUP + '''
from app.product_composition import worker_legacy_evidence as resolver
name = 'game_evidence.receive_legacy'
original_handler = lambda *_: None
original_prepare = lambda payload: payload
command_gateway.register_command(name, original_handler, prepare=original_prepare)
tasks.record_operation_attempt = lambda *_a, **_kw: (True, {'original':1}, 'token', 1)
tasks.execute_command = lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError('unexpected publication'))
events = []
tasks.record_operation_retry = lambda op, token, error, **kw: events.append((op, token, str(error), kw))
try: tasks.execute_background_command.run('retained', name, {'original':1})
except ValueError as error: assert str(error) == 'Duplicate command: ' + name
else: raise AssertionError('preexisting registration accepted')
assert events == [('retained','token','Duplicate command: ' + name,{'retryable':False,'background':True})]
assert command_gateway._handlers[name] is original_handler
assert command_gateway._preparers[name] is original_prepare
assert name not in resolver._loaded
''')
