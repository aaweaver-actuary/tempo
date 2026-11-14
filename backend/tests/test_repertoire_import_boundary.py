"""Public repertoire values must not import the concrete storage adapter."""

import os
import subprocess
import sys


def test_public_repertoire_imports_without_storage():
    script = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == "tempo_storage" or name.startswith("tempo_storage."):
        raise AssertionError("storage import: " + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from app.repertoire import RepertoireSnapshotBoundExceeded, PreparedPublishedRepertoire, build_index
assert issubclass(RepertoireSnapshotBoundExceeded, ValueError)
"""
    subprocess.run([sys.executable, "-c", script], env=os.environ.copy(), check=True)


def test_snapshot_bound_storage_compatibility_identity():
    from tempo_storage.authored_repertoires.product_snapshot import (
        RepertoireSnapshotBoundExceeded as storage_error,
    )

    from app.repertoire import RepertoireSnapshotBoundExceeded

    assert storage_error is RepertoireSnapshotBoundExceeded
