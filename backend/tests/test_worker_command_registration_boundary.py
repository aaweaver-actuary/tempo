"""The production worker registers these commands without HTTP routers."""
import os
import subprocess
import sys


def test_worker_registers_prefix_and_checkpoint_without_http_or_starting_providers():
    script = '''
import importlib.abc
import socket
import subprocess
import sys
class RejectHTTP(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"app.opening_evidence_api", "app.canonical_prefix_api"}:
            raise AssertionError("worker imported HTTP router: " + fullname)
def forbidden(*args, **kwargs):
    raise AssertionError("worker import attempted network or provider startup")
sys.meta_path.insert(0, RejectHTTP())
socket.socket.connect = forbidden
socket.create_connection = forbidden
subprocess.Popen = forbidden
from app import tasks, command_gateway, opening_evidence_commands, canonical_prefix_commands
handlers = command_gateway._handlers
preparers = command_gateway._preparers
assert handlers["opening_evidence.checkpoint"] is opening_evidence_commands.commit_standalone_checkpoint
assert preparers["opening_evidence.checkpoint"] is opening_evidence_commands.prepare_standalone_checkpoint
assert handlers["repertoire.canonical_prefix.preview"] is canonical_prefix_commands.preview_command
assert handlers["repertoire.canonical_prefix.save"] is canonical_prefix_commands.save_prefix
for task in (tasks.execute_foreground_command, tasks.execute_background_command,
             tasks.progress_study_queue, tasks.execute_background_slice):
    assert task.name == "app.tasks." + task.__name__
assert "app.opening_evidence_api" not in sys.modules
assert "app.canonical_prefix_api" not in sys.modules
'''
    subprocess.run([sys.executable, '-c', script], env=os.environ.copy(), check=True, timeout=20)


def test_engine_fairness_fixture_preserves_later_worker_command_resolution():
    script = '''
import importlib.abc
import os
import socket
import subprocess
import sys
from contextlib import contextmanager
from unittest.mock import patch
import psycopg
class RejectMain(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "app.main":
            raise AssertionError("engine fixture imported app.main")
def forbidden(*args, **kwargs):
    raise AssertionError("registration proof attempted database, network or provider execution")
sys.meta_path.insert(0, RejectMain())
socket.socket.connect = forbidden
socket.create_connection = forbidden
subprocess.Popen = forbidden
psycopg.connect = forbidden
from database.tests import check_postgres_engine_fairness as engine_fixture
from database.tests import check_postgres_graph_retention as fixtures
from app import command_gateway, game_analysis_commands
from app.product_composition.worker_game_commands import resolve_worker_game_command
from tempo_legacy_database.infrastructure import database_admission

class FixtureBoundaryReached(Exception):
    pass
parent_url = "postgresql://unused.invalid/registration-proof"
@contextmanager
def stop_before_database():
    assert fixtures.DATABASE_URL == parent_url
    assert database_admission() is engine_fixture.activity_gate
    raise FixtureBoundaryReached
    yield
with patch.dict(os.environ, {"TEMPO_TEST_INSTANCE": "disposable"}), patch.object(
        fixtures, "owned_fixture_database", stop_before_database):
    try:
        engine_fixture.proof_engine_fairness(parent_url)
    except FixtureBoundaryReached:
        pass
    else:
        raise AssertionError("engine proof did not reach its owned database boundary")

expected = {
    "games.analysis.position.claim": game_analysis_commands.claim_game_analysis_position,
    "games.analysis.position.release": game_analysis_commands.release_position_report,
    "games.analysis.position.report": game_analysis_commands.publish_position_report,
}
handlers = command_gateway._handlers
assert {name for name in handlers if name.startswith("games.analysis.")} == set(expected)
for name, handler in expected.items():
    assert handlers[name] is handler
    resolve_worker_game_command(name)
    assert handlers[name] is handler
assert not command_gateway.is_command_registered("games.analysis.finalize.admit")
resolve_worker_game_command("games.analysis.finalize.admit")
from app import game_analysis_publication
expected["games.analysis.finalize.admit"] = game_analysis_publication.admit_game_analysis_publication
for _ in range(2):
    for name, handler in expected.items():
        resolve_worker_game_command(name)
        assert handlers[name] is handler
assert not command_gateway.is_command_registered("games.analysis.manual.admit")
assert "app.main" not in sys.modules
'''
    subprocess.run([sys.executable, '-c', script], env=os.environ.copy(), check=True, timeout=20)
