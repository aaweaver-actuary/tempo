"""Only an eligible retained Games slice loads its optional implementation."""
import os
import subprocess
import sys
import pytest


def run_fresh(body):
    guard = '''
import socket
def no_network(*args, **kwargs): raise AssertionError('unexpected network')
socket.socket.connect = no_network
socket.create_connection = no_network
'''
    subprocess.run([sys.executable, '-c', guard + body], env=os.environ.copy(), check=True, timeout=20)


SETUP = '''
from contextlib import nullcontext
from app import tasks
tasks.activity_gate.essential_study_progress = lambda *_: nullcontext()
tasks.activity_gate.background_job = lambda *_a, **_kw: nullcontext()
tasks.activity_gate.check_background_admission = lambda: None
tasks.measure_handler = lambda *_a, **_kw: nullcontext()
tasks.defer_paused_defensive_task = lambda _: False
tasks.study_only_enabled = lambda: False
tasks.current_delivery = lambda _: True
'''


def test_essential_queue_cold_worker_denies_both_optional_slice_modules():
    run_fresh('''
import importlib.abc, sys
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'app.services.postgres_game_sync', 'app.services.postgres_game_sync_windows',
            'app.services.postgres_next_opponent', 'app.services.postgres_game_derivation',
            'app.services.postgres_game_repertoire', 'app.services.postgres_game_findings',
            'app.services.postgres_game_misses', 'app.services.postgres_game_events',
            'app.services.postgres_game_features', 'app.services.postgres_game_priorities'}:
            raise AssertionError(fullname)
sys.meta_path.insert(0, Deny())
''' + SETUP + '''
claimed = {'kind':'daily_queue','id':'study','generation':3,'lease_token':'lease','payload':{}}
tasks.claim_task = lambda kind: claimed if kind == 'daily_queue' else None
tasks.execute_postgres_queue_refresh_slice = lambda value: False if value is claimed else (_ for _ in ()).throw(AssertionError('changed task'))
assert tasks.progress_study_queue.run() is False
assert 'app.services.postgres_game_sync' not in sys.modules
assert 'app.services.postgres_game_sync_windows' not in sys.modules
''')


def test_retained_sync_slices_use_original_fenced_delivery():
    run_fresh(SETUP + '''
from types import SimpleNamespace
from app.services import postgres_game_sync as record, postgres_game_sync_windows as window
observed = []
record.background_lease = window.background_lease = lambda: nullcontext()
record.connection = window.connection = lambda **_: nullcontext(object())
record.lock_current_slice = window.lock_current_slice = lambda db, task: observed.append(task) or False
window._load_window_and_request = lambda task: None
base = {'id':'retained','generation':9,'lease_token':'original-lease'}
record_task = {**base, 'kind':'game_sync_record', 'payload': {'job_id':'job', 'record': {
 'provider':'lichess','username':'alice','provider_game_id':'g','played_at':'2026-09-27T12:00:00+00:00',
 'speed':'rapid','rated':True,'color':'white','result':'1-0',
 'start_fen':'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1','uci_moves':['e2e4']}}}
window_task = {**base, 'kind':'game_sync_window', 'payload':{'window_id':'window'}}
assert tasks._execute_claimed_background_slice(record_task, None) is False
assert tasks._execute_claimed_background_slice(window_task, None) is False
assert observed == [record_task, window_task] and observed[0] is record_task and observed[1] is window_task
''')


def test_stale_delivery_does_not_import_and_missing_slice_records_failure():
    run_fresh('''
import importlib.abc, sys
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'app.services.postgres_game_sync': raise ImportError('synthetic missing slice')
sys.meta_path.insert(0, Deny())
''' + SETUP + '''
claimed = {'kind':'game_sync_record','id':'retained','generation':9,'lease_token':'original-lease','payload':{}}
events = []
tasks.current_delivery = lambda value: events.append(('fence', value)) or False
tasks.record_stale_delivery = lambda value: events.append(('stale', value))
assert tasks._execute_claimed_background_slice(claimed, None) is False
assert [x[0] for x in events] == ['fence', 'stale']
assert 'app.services.postgres_game_sync' not in sys.modules
events.clear()
tasks.current_delivery = lambda value: events.append(('fence', value)) or True
tasks.fail_task = lambda ident, generation, token, error: events.append(('failed', ident, generation, token, str(error)))
try:
    tasks._execute_claimed_background_slice(claimed, None)
except ImportError as error:
    assert str(error) == 'synthetic missing slice'
else: raise AssertionError('missing slice was hidden')
assert events == [('fence', claimed), ('failed', 'retained', 9, 'original-lease', 'synthetic missing slice')]
''')


SLICES = [
    ('next_opponent_profile', 'postgres_next_opponent'),
    ('game_derivation_positions', 'postgres_game_derivation'),
    ('game_derivation_compare', 'postgres_game_repertoire'),
    ('game_derivation_findings', 'postgres_game_findings'),
    ('game_derivation_misses', 'postgres_game_misses'),
    ('game_derivation_events', 'postgres_game_events'),
    ('game_derivation_features', 'postgres_game_features'),
    ('game_derivation_priorities', 'postgres_game_priorities'),
]


@pytest.mark.parametrize('kind,module', SLICES)
def test_derivation_retained_slice_preserves_fence_and_owner_task(kind, module):
    run_fresh(SETUP + f"""
import importlib
owner = importlib.import_module('app.services.{module}')
observed = []
owner.connection = lambda **_: nullcontext(object())
owner.background_lease = lambda: nullcontext()
owner.background_read_connection = lambda: nullcontext(object())
owner.lock_current_slice = lambda db, task: observed.append(task) or False
if '{module}' == 'postgres_next_opponent': owner._load_inputs = lambda *_: None
if '{module}' == 'postgres_game_derivation': owner.storage.game_moves = lambda *_: None
if '{module}' == 'postgres_game_repertoire':
    owner.game_scope_generation = lambda *_a, **_kw: 1
    owner._prepared_comparison = lambda *_: (None, None, [])
if '{module}' == 'postgres_game_findings':
    owner.game_scope_generation = lambda *_a, **_kw: 1
    owner.storage.comparison_generation = lambda *_: 1
    owner.storage.prepared_item = lambda *_: None
    owner._prepared_items = lambda *_: (None, [])
if '{module}' == 'postgres_game_misses': owner.storage.next_miss_event = lambda *_: None
if '{module}' == 'postgres_game_events': owner.refresh_gameplay_events = lambda *_a, **_kw: []
if '{module}' == 'postgres_game_features': owner.refresh_game_features = lambda *_a, **_kw: None
if '{module}' == 'postgres_game_priorities': owner.storage.game_color = lambda *_: None
claimed = {{'kind':'{kind}', 'id':'retained', 'generation':9, 'lease_token':'lease',
           'payload':{{'game_id':'game', 'derivation_version':3, 'account':'alice'}}}}
fences = []
tasks.current_delivery = lambda value: fences.append(value) or True
assert tasks._execute_claimed_background_slice(claimed, None) is False
assert fences == [claimed] and fences[0] is claimed
assert observed == [claimed] and observed[0] is claimed
""")


@pytest.mark.parametrize('kind,module', SLICES)
def test_derivation_stale_or_missing_slice_retains_failure_identity(kind, module):
    run_fresh(f"""
import importlib.abc, sys
class Deny(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'app.services.{module}': raise ImportError('missing slice')
sys.meta_path.insert(0, Deny())
""" + SETUP + f"""
claimed = {{'kind':'{kind}', 'id':'retained', 'generation':9, 'lease_token':'lease', 'payload':{{}}}}
events = []
tasks.current_delivery = lambda value: events.append(('fence', value)) or False
tasks.record_stale_delivery = lambda value: events.append(('stale', value))
assert tasks._execute_claimed_background_slice(claimed, None) is False
assert [event[0] for event in events] == ['fence', 'stale']
assert 'app.services.{module}' not in sys.modules
events.clear()
tasks.current_delivery = lambda value: events.append(('fence', value)) or True
tasks.fail_task = lambda ident, generation, token, error: events.append(('failed', ident, generation, token, str(error)))
try: tasks._execute_claimed_background_slice(claimed, None)
except ImportError as error: assert str(error) == 'missing slice'
else: raise AssertionError('missing slice was hidden')
assert events == [('fence', claimed), ('failed', 'retained', 9, 'lease', 'missing slice')]
""")
