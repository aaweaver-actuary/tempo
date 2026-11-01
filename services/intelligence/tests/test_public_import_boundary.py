"""Pure Intelligence exports do not require HTTP or database construction."""
import subprocess
import sys


def _run(code: str) -> None:
    result = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_pure_intelligence_root_rejects_http_and_database_imports():
    _run('''
import sys
from importlib.abc import MetaPathFinder
class Guard(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('tempo_intelligence.api', 'tempo_intelligence_database', 'fastapi', 'psycopg')):
            raise AssertionError('Unexpected construction import: ' + fullname)
sys.meta_path.insert(0, Guard())
def audit(event, args):
    if event in {'socket.connect', 'socket.bind', 'subprocess.Popen'}:
        raise AssertionError('Unexpected side effect: ' + event)
sys.addaudithook(audit)
from tempo_intelligence import Configuration, analysis_v1, run_analysis_work
assert Configuration.__name__ == 'Configuration'
assert analysis_v1.Manifest and callable(run_analysis_work)
assert 'tempo_intelligence.api' not in sys.modules
assert not any(name.startswith('tempo_intelligence_database') for name in sys.modules)
''')


def test_lazy_create_app_keeps_identity_and_missing_dependency_errors():
    _run('''
from tempo_intelligence import create_app
from tempo_intelligence.api import create_app as actual
assert create_app is actual
''')
    _run('''
import sys
from importlib.abc import MetaPathFinder
class Guard(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'tempo_intelligence.api':
            raise ModuleNotFoundError('required HTTP composition missing', name=fullname)
sys.meta_path.insert(0, Guard())
import tempo_intelligence
try:
    tempo_intelligence.create_app
except ModuleNotFoundError as error:
    assert error.name == 'tempo_intelligence.api'
else:
    raise AssertionError('missing composition hidden')
try:
    tempo_intelligence.no_such_export
except AttributeError:
    pass
else:
    raise AssertionError('unknown attribute accepted')
''')


def test_run_analysis_work_closes_store_and_unstarted_client_on_startup_failure(monkeypatch):
    import pytest
    import tempo_intelligence
    from tempo_intelligence_database.adapters import store as owner
    events = []
    class Store:
        def __init__(self, configuration): events.append('created')
        def validate_startup(self): raise RuntimeError('unavailable')
        def close(self): events.append('store closed')
    class Client:
        def close(self): events.append('client closed')
    monkeypatch.setattr(owner, 'Store', Store)
    with pytest.raises(RuntimeError, match='unavailable'):
        tempo_intelligence.run_analysis_work(object(), 'run', client=Client())
    assert events == ['created', 'client closed', 'store closed']
