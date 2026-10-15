"""Thin-wrapper regressions reuse the existing Learning inventory checker."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def runner():
    spec = importlib.util.spec_from_file_location("remediation_native_runner", HERE / "run_postgres.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("options", ["-k partial", "-k no_matching_test", "--maxfail=1"])
def test_normal_suite_rejects_partial_selection(options):
    result = subprocess.run([sys.executable, str(HERE.parent / "run_suite.py"), "pure"],
        env={**os.environ, "PYTEST_ADDOPTS": options}, capture_output=True, text=True)
    assert result.returncode == 2 and "inherited PYTEST_ADDOPTS" in result.stderr


def test_native_rejected_retry_invalidates_old_current_success_without_creating_resources(tmp_path, monkeypatch):
    module = runner()
    (tmp_path / "native.json").write_text('{"complete":true}')
    monkeypatch.setattr(sys, "argv", [str(HERE / "run_postgres.py"), str(tmp_path)])
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
    monkeypatch.setattr(sys, "argv", [str(HERE / "run_postgres.py"), str(tmp_path)])
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    assert module.main() == 1
    report = json.loads((tmp_path / "native.json").read_text())
    assert not report["complete"] and "injected startup failure" in report["error"]
    assert ("close" in calls) is (created and cleanup_failure != "inspect")
    assert report["cleanup_verified"] is (cleanup_failure is None)
    assert (report["cleanup_error"] is None) is (cleanup_failure is None)


@pytest.mark.parametrize("form", ["--junitxml", "--junit-xml"])
def test_normal_suite_routes_complete_junit_inventory(tmp_path, form):
    destination = tmp_path / "results with spaces.xml"
    environment = dict(os.environ)
    environment.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run([sys.executable, str(HERE.parent / "run_suite.py"), "pure", form, str(destination)],
        env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert destination.is_file() and '"complete": true' in result.stdout
