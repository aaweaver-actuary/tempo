"""HTTP composition does not register worker command handlers."""

import os
import subprocess
import sys


def test_http_routers_import_without_worker_registration_or_main():
    script = """
import importlib.abc, socket, subprocess, sys
def forbidden(*args, **kwargs): raise AssertionError("network or process startup")
socket.socket.connect = forbidden
socket.create_connection = forbidden
subprocess.Popen = forbidden
class Reject(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'app.main', 'app.tasks', 'app.canonical_prefix_commands', 'app.opening_evidence_commands'}:
            raise AssertionError('unexpected import: ' + fullname)
sys.meta_path.insert(0, Reject())
from app import opening_evidence_api
from app.contracts.opening_evidence import OpeningEvidenceCheckpoint
assert opening_evidence_api.OpeningEvidenceCheckpoint is OpeningEvidenceCheckpoint
checkpoint = next(route for route in opening_evidence_api.router.routes if route.path == '/api/opening-evidence/checkpoints')
assert checkpoint.methods == {'POST'}
assert checkpoint.endpoint.__annotations__['request'] is OpeningEvidenceCheckpoint
assert not {'app.main', 'app.tasks', 'app.canonical_prefix_commands', 'app.opening_evidence_commands'} & sys.modules.keys()
"""
    subprocess.run(
        [sys.executable, "-c", script], env=os.environ.copy(), check=True, timeout=20
    )
