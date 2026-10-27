"""Public lichess identity/signatures; no provider acquisition invoked."""
import importlib
import inspect
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
PATHS=[str(ROOT/'backend')]+[str(p/'src') for prefix in ('packages','contracts') for p in (ROOT/prefix).iterdir() if (p/'src').is_dir()]
sys.path[:0]=PATHS


def test_public_provider_preserves_class_and_operation_identity():
    public=importlib.import_module('app.games.providers.interfaces.lichess')
    acquisition=importlib.import_module('app.games.providers.lichess_acquisition')
    donor=importlib.import_module('app.games.providers.lichess')
    for name in ['LichessAcquisitionError', 'LichessGamePage', 'LichessSourceRecord', 'fetch_lichess_page']:assert getattr(public,name) is getattr(acquisition,name)
    for name in ['fetch_lichess_games', 'fetch_lichess_games_window']:assert getattr(public,name) is getattr(donor,name)
    assert sorted(public.__all__)==sorted(['LichessAcquisitionError', 'LichessGamePage', 'LichessSourceRecord', 'fetch_lichess_page', 'fetch_lichess_games', 'fetch_lichess_games_window'])


def test_public_bounded_operation_retains_keyword_only_injected_client():
    public=importlib.import_module('app.games.providers.interfaces.lichess')
    operation=public.fetch_lichess_page
    assert inspect.iscoroutinefunction(operation)
    parameters=inspect.signature(operation).parameters
    assert parameters['client'].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters['account'].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters['window'].kind is inspect.Parameter.KEYWORD_ONLY


def test_provider_cold_import_opens_no_connection_process_or_http_client():
    code="""
import sys,socket,subprocess,httpx
sys.path[:0]=PATHS
from unittest.mock import patch
def deny(*a,**kw):raise AssertionError('startup forbidden')
with patch.object(socket.socket,'connect',deny),patch.object(subprocess,'Popen',deny),patch.object(httpx.AsyncClient,'__init__',deny):
 import app.games.providers.interfaces.lichess
assert 'app.main' not in sys.modules
""".replace('PATHS',repr(PATHS))
    subprocess.run([sys.executable,'-c',code],check=True,timeout=5)
