"""Pure plan/supervision checks; never start Docker, build or native testing."""
import importlib.util
import json
import hashlib
import io
import zipfile
from pathlib import Path
import signal
import subprocess
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("legacy_bridge_qualification", ROOT / "scripts/qualify_legacy_bridge.py")
qualification = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qualification)


def test_finite_plan_pins_six_artifacts_exact_test_counts_caps_and_five_minute_cleanup():
    plan = qualification.describe()
    assert plan["artifact_test_total"] == 255 and plan["native_test_total"] == 5
    assert plan["cleanup_reserve_seconds"] == 300
    assert plan["work_seconds"] == {"artifacts": 900, "native": 600}
    assert plan["native_total"] == dict(containers=2, cpus=2, memory_mib=1024, pids=384, owned_internal_networks=1)
    assert len(plan["own_packages"]) == 6
    assert len(set(plan["native_tests"])) == 5


@pytest.mark.parametrize("image", ["python:3.12-slim", "latest", "sha256:wrong"])
def test_helper_rejects_mutable_or_unknown_images_before_any_resource_call(image):
    with pytest.raises(ValueError): qualification.helper_arguments(image, "owned", Path("/tmp/owned"), "artifacts")


def test_helper_caps_cover_all_descendants_and_mount_only_source_and_owned_evidence():
    args = qualification.helper_arguments("sha256:" + "a" * 64, "owned-helper", Path("/tmp/owned-evidence"), "native",
        network="owned-internal-network", env_file=Path("/tmp/owned.env"))
    assert args[args.index("--cpus") + 1] == "1"
    assert args[args.index("--memory") + 1] == args[args.index("--memory-swap") + 1] == "768m"
    assert args[args.index("--pids-limit") + 1] == "128"
    assert args[args.index("--network") + 1] == "owned-internal-network"
    assert not any("docker.sock" in value for value in args)
    assert len([value for value in args if value.startswith("type=bind")]) == 2
    assert any("target=/source,readonly" in value for value in args)


def test_deadline_terminates_the_host_process_group_inside_the_work_budget(monkeypatch):
    calls = []
    class Child:
        pid = 41234
        def wait(self, timeout):
            calls.append(("wait", timeout))
            if len([item for item in calls if item[0] == "wait"]) == 1:
                raise subprocess.TimeoutExpired("synthetic", timeout)
            return -15
    def popen(command, **kwargs):
        assert kwargs["start_new_session"] is True
        return Child()
    monkeypatch.setattr(qualification.subprocess, "Popen", popen)
    monkeypatch.setattr(qualification.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(qualification.os, "killpg", lambda pid, signum: calls.append((pid, signum)))
    with pytest.raises(subprocess.TimeoutExpired): qualification.Deadline(40, {}).run(["synthetic"])
    assert calls == [("wait", 30.0), (41234, signal.SIGTERM), ("wait", 5)]


def test_native_controller_requires_explicit_window_and_managed_budget_before_any_docker(monkeypatch):
    monkeypatch.delenv("TEMPO_DEVELOPMENT_BUDGET_TOKEN", raising=False)
    monkeypatch.setattr(qualification.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("subprocess started"))
    with pytest.raises(RuntimeError, match="approved finite window"):
        qualification.controller(SimpleNamespace(approved_window=False))


def test_helper_refuses_missing_or_wrong_cgroup_caps(monkeypatch):
    values = {"cpu.max": "100000 100000", "memory.max": str(768 * 1024 * 1024), "memory.swap.max": "0", "pids.max": "128"}
    monkeypatch.setattr(qualification, "Path", lambda value: SimpleNamespace(read_text=lambda: values[value.rsplit("/", 1)[-1]]))
    qualification.verify_helper_caps()
    values["pids.max"] = "max"
    with pytest.raises(ValueError): qualification.verify_helper_caps()


def test_pytest_first_failure_stops_the_window_with_no_complete_inventory(tmp_path):
    commands = []
    class Timer:
        def run(self, command, **_options):
            commands.append(command)
            assert "--maxfail=1" in command
            if "--collect-only" in command:
                Path(command[3]).write_text(json.dumps({"collected": ["first", "not-executed"]}))
                return
            Path(command[3]).write_text(json.dumps({"collected": ["first", "not-executed"],
                                                  "executed": ["first"], "exit_code": 1}))
            raise RuntimeError("first test failed")
    with pytest.raises(RuntimeError, match="first test failed"):
        qualification.run_suite(Timer(), "python", tmp_path, "failed", ROOT / "services/learning/remediation",
                                ("tests",), 2, True)
    assert len(commands) == 2
    assert json.loads((tmp_path / "failed-execution.json").read_text())["exit_code"] == 1


def test_native_pinned_tool_requires_retained_artifact_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(qualification.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(qualification.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network fallback"))
    with pytest.raises(RuntimeError, match="qualified pinned tool"):
        qualification.pinned_uv(tmp_path, "native", None, tmp_path)


def test_pinned_tool_rejects_changed_wheel_before_extraction_or_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(qualification.platform, "machine", lambda: "aarch64")
    directory = tmp_path / "tools/uv-0.9.3"
    directory.mkdir(parents=True)
    (directory / "vendor.whl").write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="wheel changed"):
        qualification.pinned_uv(tmp_path, "artifacts", None, tmp_path)


def test_pinned_tool_extracts_only_verified_binary_and_native_reuses_it_offline(tmp_path, monkeypatch):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("uv.data/scripts/uv", b"synthetic binary, never executed")
        archive.writestr("uv.data/scripts/uvx", b"not extracted")
    data = content.getvalue()
    url = "https://files.pythonhosted.org/synthetic-reviewed.whl"
    monkeypatch.setattr(qualification.platform, "machine", lambda: "aarch64")
    monkeypatch.setitem(qualification.UV_WHEELS, "aarch64", (url, hashlib.sha256(data).hexdigest()))
    class Download:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def geturl(self): return url
        def read(self, maximum):
            assert maximum == 24 * 1024 * 1024 + 1
            return data
    monkeypatch.setattr(qualification.urllib.request, "urlopen", lambda source, timeout:
        Download() if source == url and timeout == 20 else pytest.fail("unreviewed download"))
    timer = SimpleNamespace(capture=lambda argv, maximum: SimpleNamespace(stdout="uv 0.9.3\n"))
    binary = qualification.pinned_uv(tmp_path, "artifacts", timer, tmp_path)
    assert Path(binary).read_bytes() == b"synthetic binary, never executed"
    assert not (Path(binary).parent / "uvx").exists()
    monkeypatch.setattr(qualification.urllib.request, "urlopen", lambda *a, **k: pytest.fail("native network"))
    assert qualification.pinned_uv(tmp_path, "native", timer, tmp_path) == binary
