"""The normal-mode fixture patches the owner without importing the task root."""
import os
from pathlib import Path
import subprocess
import sys


def test_normal_study_fixture_does_not_import_tasks_and_patches_owner():
    root = Path(__file__).resolve().parents[2]
    code = '''
import builtins, importlib.util, sys
from pathlib import Path
from pytest import MonkeyPatch
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == "app.tasks" or (name == "app" and "tasks" in (kwargs.get("fromlist") or (args[2] if len(args) > 2 else ()))):
        raise AssertionError("fixture imported task root")
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
spec = importlib.util.spec_from_file_location("fixture_conftest", Path("backend/tests/conftest.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with MonkeyPatch.context() as patch:
    module.normal_study_mode.__wrapped__(patch)
    from app.services import study_mode
    assert study_mode.study_only_enabled() is False
    assert "app.tasks" not in sys.modules
'''
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(root / 'backend'), str(root / 'database')])}
    subprocess.run([sys.executable, '-c', code], cwd=root, env=env, check=True)


def test_tasks_compatibility_seam_reads_current_policy(monkeypatch):
    from app import tasks
    from app.services import study_mode
    monkeypatch.setattr(study_mode, 'study_only_enabled', lambda database=None: True)
    assert tasks.study_only_enabled() is True
    monkeypatch.setattr(study_mode, 'study_only_enabled', lambda database=None: False)
    assert tasks.study_only_enabled() is False
