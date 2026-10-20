"""Direct CLI source discovery, without importing the application or opening storage."""
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINTS = (
    "scripts/benchmark_storage_reads.py",
    "scripts/recover-opening-publication.py",
    "scripts/evaluate_opening_rankings.py",
    "scripts/show_opening_frontier.py",
    "scripts/stalemate_swindles.py",
    "scripts/check_redis_background_wakes.py",
    "scripts/check_redis_socket_deadlines.py",
)


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
