"""Only an eligible retained Games slice loads its optional implementation."""
import os
import subprocess
import sys


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
        if fullname in {'app.services.postgres_game_sync', 'app.services.postgres_game_sync_windows'}:
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
