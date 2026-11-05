"""Retained product feedback is registered only on its worker delivery."""

from test_worker_legacy_evidence_registration import SETUP, run_fresh


def test_essential_queue_does_not_import_product_feedback():
    run_fresh(
        """
import importlib.abc, sys
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'app.product_composition.feedback_commands': raise AssertionError(fullname)
sys.meta_path.insert(0, Deny())
"""
        + SETUP
        + """
tasks.activity_gate.essential_study_progress = lambda *_: nullcontext()
tasks.defer_paused_defensive_task = lambda _: False
tasks.current_delivery = lambda _: True
claimed = {'kind':'daily_queue','id':'study','generation':1,'lease_token':'lease','payload':{}}
tasks.claim_task = lambda kind: claimed
tasks.execute_postgres_queue_refresh_slice = lambda task: False if task is claimed else (_ for _ in ()).throw(AssertionError('changed task'))
assert tasks.progress_study_queue.run() is False
assert 'app.product_composition.feedback_commands' not in sys.modules
"""
    )


def test_retained_feedback_resolves_after_receipt_with_original_identity():
    run_fresh(
        SETUP
        + """
name = 'game_evidence.product_accept'; original = {'game_id':'game','occurrence_ply':7}
events = []
def attempt(op, command, payload, **kw):
    assert not command_gateway.is_command_registered(name)
    events.append(('attempt',op,command,payload)); return True,payload,'original-token',1
tasks.record_operation_attempt = attempt
def execute(op, command, payload, **kw):
    from app.product_composition import feedback_commands as owner
    assert command_gateway._handlers[name] is owner._receive_command
    assert command_gateway._preparers[name] is owner._prepare_command
    events.append(('execute',op,command,payload,kw)); return {'accepted':True}
tasks.execute_command = execute
assert tasks.execute_background_command.run('retained',name,original) == {'accepted':True}
assert events == [('attempt','retained',name,original),('execute','retained',name,original,{'background':True,'attempt_token':'original-token'})]
"""
    )


def test_concurrent_selection_import_failure_retry_and_core_nonblocking():
    run_fresh(
        """
import importlib.abc, threading
from concurrent.futures import ThreadPoolExecutor
class DenyOnce(importlib.abc.MetaPathFinder):
    fail = True
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'app.product_composition.feedback_commands' and self.fail:
            self.fail = False; raise ImportError('temporary unavailable')
import sys
sys.meta_path.insert(0, DenyOnce())
"""
        + SETUP
        + """
from app.product_composition import worker_product_feedback as resolver
name = 'game_evidence.product_accept'
try: resolver.resolve_worker_product_feedback(name)
except ImportError: pass
else: raise AssertionError('failure hidden')
assert not resolver._loaded and not command_gateway.is_command_registered(name)
with ThreadPoolExecutor(max_workers=4) as pool:
    list(pool.map(resolver.resolve_worker_product_feedback,[name]*8))
assert resolver._loaded and command_gateway.is_command_registered(name)
with resolver._lock:
    done = threading.Event()
    worker = threading.Thread(target=lambda: (resolver.resolve_worker_product_feedback('queue.issue_head'),done.set()))
    worker.start(); assert done.wait(2)
worker.join(2); assert not worker.is_alive()
"""
    )


def test_finished_conflict_and_failure_keep_original_attempt_fence():
    run_fresh(
        SETUP
        + """
name = 'game_evidence.product_accept'
tasks.record_operation_attempt = lambda *_a, **_kw: (False, {}, None, 1)
tasks.resolve_worker_product_feedback = lambda *_: (_ for _ in ()).throw(AssertionError('unexpected resolve'))
assert tasks.execute_background_command.run('done',name,{}) is None
def conflict(*_a, **_kw): raise command_gateway.CommandConflict('changed')
tasks.record_operation_attempt = conflict
try: tasks.execute_background_command.run('conflict',name,{})
except command_gateway.CommandConflict: pass
else: raise AssertionError('conflict hidden')
tasks.record_operation_attempt = lambda *_a, **_kw: (True, {'original':1}, 'token', 1)
tasks.resolve_worker_product_feedback = lambda *_: (_ for _ in ()).throw(ImportError('unavailable'))
events = []
tasks.record_operation_retry = lambda op, token, error, **kw: events.append((op,token,str(error),kw))
try: tasks.execute_background_command.run('retained',name,{'original':1})
except ImportError: pass
else: raise AssertionError('failure hidden')
assert events == [('retained','token','unavailable',{'retryable':False,'background':True})]
"""
    )


def test_preexisting_registration_conflicts_without_override():
    run_fresh(
        SETUP
        + """
from app.product_composition import worker_product_feedback as resolver
name = 'game_evidence.product_accept'
handler = lambda *_: None
prepare = lambda payload: payload
command_gateway.register_command(name,handler,prepare=prepare)
try: resolver.resolve_worker_product_feedback(name)
except ValueError as error: assert str(error) == 'Duplicate command: ' + name
else: raise AssertionError('duplicate accepted')
assert command_gateway._handlers[name] is handler and command_gateway._preparers[name] is prepare
assert not resolver._loaded
"""
    )
