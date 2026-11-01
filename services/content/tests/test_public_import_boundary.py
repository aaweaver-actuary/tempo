"""The pure root does not eagerly load the concrete Content store."""
import subprocess
import sys


def _run(code: str) -> None:
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_pure_package_import_does_not_load_store_or_http():
    _run('''
import sys
from importlib.abc import MetaPathFinder
class Guard(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('tempo_content_database', 'tempo_content.cli', 'psycopg')):
            raise AssertionError('Unexpected concrete import: ' + fullname)
sys.meta_path.insert(0, Guard())
from tempo_content import stable_id, SourceSnapshot
assert callable(stable_id) and SourceSnapshot.__name__ == 'SourceSnapshot'
assert not any(name.startswith('tempo_content_database') for name in sys.modules)
''')


def test_lazy_concrete_export_preserves_identity_and_dependency_errors():
    _run('''
import sys
from tempo_content import ContentService
from tempo_content_database.adapters.store import ContentService as concrete
assert ContentService is concrete
assert 'tempo_content_database.adapters.store' in sys.modules
''')
    _run('''
import sys
from importlib.abc import MetaPathFinder
class Guard(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'tempo_content_database':
            raise ModuleNotFoundError('required Content store missing', name=fullname)
sys.meta_path.insert(0, Guard())
import tempo_content
try:
    tempo_content.ContentService
except ModuleNotFoundError as error:
    assert error.name == 'tempo_content_database'
else:
    raise AssertionError('missing required store was hidden')
try:
    tempo_content.no_such_export
except AttributeError:
    pass
else:
    raise AssertionError('unknown attribute was accepted')
''')
