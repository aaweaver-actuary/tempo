"""Direct CLI source discovery, without importing the application or opening storage."""
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINTS = (
    "database/tempo_legacy_database/tooling/benchmark_storage_reads.py",
    "scripts/recover-opening-publication.py",
    "scripts/evaluate_opening_rankings.py",
    "scripts/show_opening_frontier.py",
    "database/tests/redis/check_redis_background_wakes.py",
    "database/tests/redis/check_redis_socket_deadlines.py",
)


def test_database_snapshot_cli_help_needs_no_pythonpath_or_storage(tmp_path):
    probe = r'''
import runpy
import sys
def prohibit_effects(event, arguments):
    if event in {'socket.connect', 'socket.bind', 'sqlite3.connect', 'subprocess.Popen'}:
        raise AssertionError('CLI help attempted an external effect: ' + event)
sys.addaudithook(prohibit_effects)
sys.argv = [sys.argv[1], '--help']
try:
    runpy.run_path(sys.argv[0], run_name='__main__')
except SystemExit as result:
    assert result.code == 0
else:
    raise AssertionError('CLI help did not exit')
'''
    result = subprocess.run(
        [sys.executable, '-I', '-c', probe, str(ROOT / 'database/tooling/create_sqlite_cutover_snapshot.py')],
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'usage:' in result.stdout


@pytest.mark.parametrize("entrypoint", ENTRYPOINTS)
def test_database_relocation_direct_cli_resolves_owner_without_pythonpath(entrypoint, tmp_path):
    probe = r'''
from importlib.abc import MetaPathFinder
from importlib.machinery import PathFinder
from pathlib import Path
import runpy
import sys

repository = Path(sys.argv[1])
entrypoint = repository / sys.argv[2]
assert str(repository / "database") not in sys.path
assert str(repository / "backend") not in sys.path

def check_source_roots():
    database = PathFinder.find_spec("tempo_legacy_database")
    application = PathFinder.find_spec("app")
    assert database is not None, "direct CLI cannot discover centralized database package"
    assert Path(database.origin) == repository / "database/tempo_legacy_database/__init__.py"
    assert application is not None
    assert Path(application.origin) == repository / "backend/app/__init__.py"

class ApplicationBoundaryReached(BaseException):
    pass

class StopBeforeApplication(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"app", "tempo_legacy_database"}:
            check_source_roots()
            raise ApplicationBoundaryReached()

def prohibit_effects(event, arguments):
    if event in {"socket.connect", "socket.bind", "sqlite3.connect", "subprocess.Popen"}:
        raise AssertionError("bootstrap attempted an external effect: " + event)

sys.meta_path.insert(0, StopBeforeApplication())
sys.addaudithook(prohibit_effects)
try:
    runpy.run_path(str(entrypoint), run_name="direct_cli_bootstrap_probe")
except ApplicationBoundaryReached:
    pass
check_source_roots()
assert "app" not in sys.modules and "tempo_legacy_database" not in sys.modules
print("isolated direct CLI database discovery passed")
'''
    result = subprocess.run(
        [sys.executable, "-I", "-c", probe, str(ROOT), entrypoint],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_learning_study_launcher_discovers_relocated_runner_before_native_effects(tmp_path):
    probe = r'''
from importlib.abc import MetaPathFinder
from importlib.machinery import PathFinder
from pathlib import Path
import runpy
import sys
from types import ModuleType

repository = Path(sys.argv[1])
package = ModuleType("tempo_learning_database")
package.__path__ = [str(repository / "database/tempo_learning_database")]
tooling = ModuleType("tempo_learning_database.tooling")
tooling.__path__ = [str(repository / "database/tempo_learning_database/tooling")]
sys.modules[package.__name__] = package
sys.modules[tooling.__name__] = tooling
class CanonicalRunnerReached(BaseException):
    pass
class StopBeforeNativeTooling(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "tempo_learning_database.tooling.run_postgres":
            assert str(repository / "database") in sys.path
            owner = PathFinder.find_spec("tempo_learning_database")
            assert owner is not None
            assert Path(owner.origin) == repository / "database/tempo_learning_database/__init__.py"
            spec = PathFinder.find_spec(fullname, path)
            assert spec is not None
            assert Path(spec.origin) == repository / "database/tempo_learning_database/tooling/run_postgres.py"
            raise CanonicalRunnerReached()
def prohibit_effects(event, arguments):
    if event in {"socket.connect", "socket.bind", "sqlite3.connect", "subprocess.Popen"}:
        raise AssertionError("launcher bootstrap attempted an external effect: " + event)
sys.meta_path.insert(0, StopBeforeNativeTooling())
sys.addaudithook(prohibit_effects)
try:
    runpy.run_path(str(repository / "services/learning/runtime/integration/run_study.py"), run_name="study_launcher_bootstrap_probe")
except CanonicalRunnerReached:
    pass
else:
    raise AssertionError("launcher did not resolve its canonical runner")
assert sys.modules["tempo_learning_database"] is package
assert "tempo_learning_database.tooling.run_postgres" not in sys.modules
assert "run_postgres" not in sys.modules
'''
    result = subprocess.run([sys.executable, "-I", "-c", probe, str(ROOT)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
