"""Pure root exports must not load the concrete Evidence storage owner."""
import subprocess
import sys


def _run(code: str) -> None:
    result = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_pure_game_evidence_root_does_not_import_storage():
    _run('''
import sys
from importlib.abc import MetaPathFinder
class Guard(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('tempo_game_evidence_database', 'tempo_game_evidence.cli', 'psycopg')):
            raise AssertionError('Unexpected concrete import: ' + fullname)
sys.meta_path.insert(0, Guard())
from tempo_game_evidence import normalize_pgn, ComparisonSnapshot, LegacyAuthorityToken
assert callable(normalize_pgn) and ComparisonSnapshot.__name__ == 'ComparisonSnapshot'
assert LegacyAuthorityToken.__name__ == 'LegacyAuthorityToken'
assert not any(name.startswith('tempo_game_evidence_database') for name in sys.modules)
''')


def test_lazy_store_preserves_identity_and_missing_dependency_error():
    _run('''
from tempo_game_evidence import EvidenceStore
from tempo_game_evidence_database.adapters.store import EvidenceStore as actual
assert EvidenceStore is actual
''')
    _run('''
import sys
from importlib.abc import MetaPathFinder
class Guard(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'tempo_game_evidence_database':
            raise ModuleNotFoundError('required evidence store missing', name=fullname)
sys.meta_path.insert(0, Guard())
import tempo_game_evidence
try:
    tempo_game_evidence.EvidenceStore
except ModuleNotFoundError as error:
    assert error.name == 'tempo_game_evidence_database'
else:
    raise AssertionError('missing store hidden')
try:
    tempo_game_evidence.no_such_export
except AttributeError:
    pass
else:
    raise AssertionError('unknown attribute accepted')
''')
