"""Mocked native-runner guards; no database or container is started."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
OWNER = HERE.parents[1]
ROOT = OWNER.parent.parent
RUNNER = OWNER / "tooling/run_postgres.py"


def runner():
    spec = importlib.util.spec_from_file_location("remediation_native_runner", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module



def test_native_rejected_retry_invalidates_old_current_success_without_creating_resources(tmp_path, monkeypatch):
    module = runner()
    (tmp_path / "native.json").write_text('{"complete":true}')
    monkeypatch.setattr(sys, "argv", [str(RUNNER), str(tmp_path)])
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k partial")
    assert module.main() == 1
    report = json.loads((tmp_path / "native.json").read_text())
    assert not report["complete"] and report["cleanup_verified"]
    assert report["commands"] == [] and "inherited PYTEST_ADDOPTS" in report["error"]



@pytest.mark.parametrize("created,cleanup_failure", [(False, None), (True, None), (True, "inspect"), (True, "close")])
def test_partial_startup_always_attempts_verified_owned_cleanup(tmp_path, monkeypatch, created, cleanup_failure):
    module = runner()
    calls = []
    class Fixture:
        identity = "owned-fixture"
        container = "owned-fixture-postgres"
        started = False
        def start(self):
            calls.append("start")
            raise RuntimeError("injected startup failure")
        def close(self):
            calls.append("close")
            assert self.started
            if cleanup_failure == "close": raise RuntimeError("ownership mismatch")
    def docker(*arguments):
        calls.append("inspect")
        assert "name=^/owned-fixture-postgres$" in arguments
        if cleanup_failure == "inspect": raise RuntimeError("daemon unavailable")
        return SimpleNamespace(stdout="container-id\n" if created else "")
    original_load = module.load
    monkeypatch.setattr(module, "load", lambda name, path: SimpleNamespace(OwnershipFixture=Fixture, docker=docker)
        if name == "ownership_fixture" else original_load(name, path))
    monkeypatch.setattr(sys, "argv", [str(RUNNER), str(tmp_path)])
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    assert module.main() == 1
    report = json.loads((tmp_path / "native.json").read_text())
    assert not report["complete"] and "injected startup failure" in report["error"]
    assert ("close" in calls) is (created and cleanup_failure != "inspect")
    assert report["cleanup_verified"] is (cleanup_failure is None)
    assert (report["cleanup_error"] is None) is (cleanup_failure is None)
