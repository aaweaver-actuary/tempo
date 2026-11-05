"""Canonical prefix command registration is independent of its HTTP router."""

import os
import subprocess
import sys


def test_canonical_prefix_commands_register_without_http_router():
    script = """
import importlib.abc
import sys
class RejectHTTP(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "app.canonical_prefix_api":
            raise AssertionError("prefix registration imported HTTP router")
sys.meta_path.insert(0, RejectHTTP())
from app import canonical_prefix_commands as commands, command_gateway
assert command_gateway._handlers["repertoire.canonical_prefix.preview"] is commands.preview_command
assert command_gateway._handlers["repertoire.canonical_prefix.save"] is commands.save_prefix
assert "app.canonical_prefix_api" not in sys.modules
"""
    subprocess.run(
        [sys.executable, "-c", script], env=os.environ.copy(), check=True, timeout=20
    )
