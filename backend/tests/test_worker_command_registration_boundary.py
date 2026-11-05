"""The production worker registers these commands without HTTP routers."""

import os
import subprocess
import sys


def test_worker_registers_prefix_and_checkpoint_without_http_or_starting_providers():
    script = """
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
from app import tasks, command_gateway, opening_evidence_commands, canonical_prefix_commands, analysis_paste_commands
from app.contracts.command_registry import RETIRED_COMMANDS
assert 'analysis.paste.commit' not in RETIRED_COMMANDS
assert command_gateway._handlers['analysis.paste.commit'] is analysis_paste_commands.commit_analysis_paste
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
from app.services import background_diagnostics
for module in ("app.threat_analysis_commands", "app.game_analysis_commands", "app.services.engine_scheduling"):
    assert module not in sys.modules
for name in ("threat.analysis.claim", "games.analysis.claim", "games.analysis.position.report"):
    assert not command_gateway.is_command_registered(name)
"""
    subprocess.run(
        [sys.executable, "-c", script], env=os.environ.copy(), check=True, timeout=20
    )


def test_retired_game_commands_are_not_lazy_loaded():
    from app.product_composition.worker_game_commands import resolve_worker_game_command
    from app.command_gateway import is_command_registered

    for name in (
        "games.analysis.position.claim",
        "games.analysis.finalize.admit",
        "games.defensive_threats.refresh",
    ):
        resolve_worker_game_command(name)
        assert not is_command_registered(name)
