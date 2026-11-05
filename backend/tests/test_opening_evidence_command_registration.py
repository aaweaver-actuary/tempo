"""Checkpoint registration does not require the HTTP router."""

import os
import subprocess
import sys


def test_opening_checkpoint_registration_imports_without_http_router():
    script = """
import importlib.abc
import sys
class RejectHTTP(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "app.opening_evidence_api":
            raise AssertionError("checkpoint registration imported HTTP router")
sys.meta_path.insert(0, RejectHTTP())
from app import opening_evidence_commands as commands
from app import command_gateway
name = "opening_evidence.checkpoint"
assert command_gateway._handlers[name] is commands.commit_standalone_checkpoint
assert command_gateway._preparers[name] is commands.prepare_standalone_checkpoint
assert "app.opening_evidence_api" not in sys.modules
"""
    subprocess.run(
        [sys.executable, "-c", script], env=os.environ.copy(), check=True, timeout=20
    )
