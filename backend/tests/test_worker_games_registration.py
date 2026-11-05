"""Selected Games commands retain identity without eager producer imports."""

import pytest
from test_worker_legacy_evidence_registration import SETUP, run_fresh


SPECS = [
    ("games.exclusion.set", "app.game_commands", "set_game_exclusion", None),
    (
        "product_games.configure",
        "app.product_composition.game_review_commands",
        "configure",
        None,
    ),
    (
        "product_games.sync",
        "app.product_composition.game_review_commands",
        "sync",
        None,
    ),
    (
        "product_games.refresh",
        "app.product_composition.game_review_commands",
        "refresh",
        None,
    ),
    (
        "product_games.retry_feedback",
        "app.product_composition.game_review_commands",
        "retry",
        "prepare_retry",
    ),
]


@pytest.mark.parametrize("name,module,handler,preparer", SPECS)
def test_retained_game_command_selects_only_original_registration(
    name, module, handler, preparer
):
    run_fresh(
        SETUP
        + f"""
import importlib, sys
name = {name!r}; module = {module!r}; original = {{'immutable':'payload'}}; seen = []
assert module not in sys.modules
def attempt(op, command, payload, **kw):
    assert module not in sys.modules
    seen.append(('attempt',op,command,payload)); return True,payload,'original-token',1
tasks.record_operation_attempt = attempt
def execute(op, command, payload, **kw):
    owner = importlib.import_module(module)
    assert command_gateway._handlers[name] is getattr(owner,{handler!r})
    if {preparer!r} is not None: assert command_gateway._preparers[name] is getattr(owner,{preparer!r})
    seen.append(('execute',op,command,payload,kw)); return {{'accepted':True}}
tasks.execute_command = execute
assert tasks.execute_foreground_command.run('retained',name,original) == {{'accepted':True}}
assert seen == [('attempt','retained',name,original),('execute','retained',name,original,{{'attempt_token':'original-token'}})]
"""
    )


@pytest.mark.parametrize("name,module,handler,preparer", SPECS)
def test_game_command_duplicate_does_not_override_existing_registration(
    name, module, handler, preparer
):
    run_fresh(
        SETUP
        + f"""
from app.product_composition import worker_game_commands as resolver
name={name!r}; original=lambda *_:None; prepare=lambda value:value
command_gateway.register_command(name,original,prepare=prepare)
try: resolver.resolve_worker_game_command(name)
except ValueError as error: assert str(error) == 'Duplicate command: '+name
else: raise AssertionError('duplicate accepted')
assert command_gateway._handlers[name] is original and command_gateway._preparers[name] is prepare
assert name not in resolver._loaded
"""
    )


def test_essential_queue_cold_worker_denies_games_producer_closure():
    run_fresh(
        """
import importlib.abc, sys
blocked = {'app.game_sync_commands','app.game_evidence_commands','app.product_composition.feedback_commands',
'app.product_composition.game_review_commands','app.game_commands','app.game_analysis_commands','app.game_analysis_publication',
'app.services.postgres_game_sync','app.services.postgres_game_sync_windows',
'app.services.postgres_game_derivation','app.services.postgres_game_repertoire','app.services.postgres_game_findings',
'app.services.postgres_game_misses','app.services.postgres_game_events','app.services.postgres_game_features','app.services.postgres_game_priorities',
'app.services.postgres_coverage_seed','app.services.postgres_coverage_explorer','app.services.postgres_coverage_recovery',
'app.services.threat_pipeline','app.services.repertoire_coverage'}
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in blocked: raise AssertionError(fullname)
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
assert not (blocked & set(sys.modules))
"""
    )


@pytest.mark.parametrize("module", sorted({item[1] for item in SPECS}))
def test_explicit_full_factory_remains_available(module):
    entries = [
        (name, handler, preparer)
        for name, selected, handler, preparer in SPECS
        if selected == module
    ]
    run_fresh(
        SETUP
        + f"""
import importlib
owner = importlib.import_module({module!r})
owner.register_commands()
for name, handler, preparer in {entries!r}:
    assert command_gateway._handlers[name] is getattr(owner,handler)
    if preparer is not None: assert command_gateway._preparers[name] is getattr(owner,preparer)
"""
    )


def test_family_import_failure_and_parallel_core_keep_original_identity():
    run_fresh(
        """
import importlib.abc, sys, threading
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'app.game_commands': raise ImportError('missing game commands')
sys.meta_path.insert(0, Deny())
"""
        + SETUP
        + """
from app.product_composition import worker_game_commands as resolver
name = 'games.exclusion.set'
tasks.record_operation_attempt = lambda *_a, **_kw: (True, {'game_id':'g'}, 'original-token', 1)
events = []
tasks.record_operation_retry = lambda op, token, error, **kw: events.append((op,token,str(error),kw))
try: tasks.execute_foreground_command.run('retained',name,{'game_id':'g'})
except ImportError: pass
else: raise AssertionError('missing import hidden')
assert events == [('retained','original-token','missing game commands',{'retryable':False,'background':False})]
assert name not in resolver._loaded and not command_gateway.is_command_registered(name)
with resolver._lock:
    done=threading.Event()
    worker=threading.Thread(target=lambda: (resolver.resolve_worker_game_command('queue.issue_head'),done.set()))
    worker.start(); assert done.wait(2)
worker.join(2); assert not worker.is_alive()
"""
    )


@pytest.mark.parametrize(
    "name,module",
    [
        ("games.exclusion.set", "app.game_commands"),
        ("product_games.configure", "app.product_composition.game_review_commands"),
    ],
)
def test_family_import_failure_retries_and_concurrent_selection_registers_once(
    name, module
):
    run_fresh(
        f"""
import importlib.abc, sys
class DenyOnce(importlib.abc.MetaPathFinder):
    fail=True
    def find_spec(self, fullname, path=None, target=None):
        if fullname == {module!r} and self.fail:
            self.fail=False; raise ImportError('temporary unavailable')
sys.meta_path.insert(0,DenyOnce())
"""
        + SETUP
        + f"""
from concurrent.futures import ThreadPoolExecutor
from app.product_composition import worker_game_commands as resolver
name={name!r}
try: resolver.resolve_worker_game_command(name)
except ImportError: pass
else: raise AssertionError('import failure hidden')
assert name not in resolver._loaded and not command_gateway.is_command_registered(name)
with ThreadPoolExecutor(max_workers=4) as pool:
    list(pool.map(resolver.resolve_worker_game_command,[name]*8))
assert name in resolver._loaded and command_gateway.is_command_registered(name)
"""
    )


def test_terminal_and_conflicting_attempts_do_not_select_game_command():
    run_fresh(
        SETUP
        + """
name = 'product_games.sync'
tasks.record_operation_attempt = lambda *_a, **_kw: (False,{},None,1)
tasks.resolve_worker_game_command = lambda *_: (_ for _ in ()).throw(AssertionError('unexpected selection'))
assert tasks.execute_background_command.run('done',name,{}) is None
def conflict(*_a, **_kw): raise command_gateway.CommandConflict('changed')
tasks.record_operation_attempt = conflict
try: tasks.execute_background_command.run('conflict',name,{})
except command_gateway.CommandConflict: pass
else: raise AssertionError('conflict hidden')
"""
    )
