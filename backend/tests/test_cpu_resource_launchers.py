"""CPU-policy launch contracts; no Docker, database or network is contacted."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("path,memory,pids", [
    ("database/tempo_content_database/tooling/run_postgres.py", "512m", "256"),
    ("database/tempo_game_evidence_database/tooling/run_postgres.py", "384m", "256"),
    ("database/tempo_intelligence_database/tooling/run_postgres.py", "384m", "256"),
    ("database/tempo_legacy_database/tooling/learning_review/run_postgres.py", "384m", "256"),
    ("database/ownership/fixture.py", "256m", "256"),
])
def test_ordinary_database_launch_is_cpu_unrestricted_with_original_memory_pids_and_ownership(path, memory, pids, tmp_path, monkeypatch):
    original_path = list(sys.path)
    monkeypatch.setattr(sys, "path", original_path)
    monkeypatch.delitem(sys.modules, "run_suite", raising=False)
    specification = importlib.util.spec_from_file_location("cpu_launch_contract", ROOT / path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    commands = []
    owners = {}

    class CapturedLaunch(BaseException):
        pass

    def docker(*arguments, **_options):
        commands.append(arguments)
        if arguments[0] == "run":
            raise CapturedLaunch()
        for value in arguments:
            if isinstance(value, str) and value.startswith("tempo.owner="):
                owners[arguments[-1]] = value.split("=", 1)[1]
        output = ""
        if "{{.Id}}" in arguments:
            output = "sha256:" + "a" * 64
        elif arguments[0] == "image" and arguments[1] == "inspect":
            output = "sha256:" + "a" * 64
        elif "inspect" in arguments and any("Labels" in str(value) for value in arguments):
            name = arguments[arguments.index("inspect") + 1]
            output = json.dumps({"tempo.owner": owners.get(name, name), "tempo.disposable": "true"})
        return SimpleNamespace(stdout=output, stderr="", returncode=0)

    def host_command(command, **_options):
        assert command[:2] == ["git", "rev-parse"], "Unexpected real host operation"
        return SimpleNamespace(stdout="synthetic-head\n", stderr="", returncode=0)

    monkeypatch.setattr(module, "docker", docker)
    monkeypatch.setattr(subprocess, "run", host_command)
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: "synthetic-head\n")
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)
    monkeypatch.setattr(sys, "argv", [path, str(tmp_path / "evidence")])
    if hasattr(module, "socket"):
        reservation = MagicMock()
        reservation.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 54321)
        monkeypatch.setattr(module.socket, "socket", lambda *args, **kwargs: reservation)
    with pytest.raises(CapturedLaunch):
        module.OwnershipFixture().start() if path == "database/ownership/fixture.py" else module.main()
    launch = next(arguments for arguments in commands if arguments[0] == "run")
    assert "--cpus" not in launch
    assert not any(str(value).startswith(("--cpu-", "--cpuset", "--cpus=")) for value in launch)
    assert launch[launch.index("--memory") + 1] == memory
    assert launch[launch.index("--pids-limit") + 1] == pids
    assert "tempo.disposable=true" in launch
    assert any(str(value).startswith(("tempo.owner=", "tempo.contract-owner=")) for value in launch)
