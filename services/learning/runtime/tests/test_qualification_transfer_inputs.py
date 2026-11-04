"""Input closure/provenance regressions, halted before builds or allocation."""
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]
CONTRACT = "contracts/learning-transfer/pyproject.toml"


def test_installed_qualifier_copies_required_transfer_contract(monkeypatch, tmp_path):
    namespace = runpy.run_path(str(ROOT / "services/learning/runtime/qualify.py"))
    original_run = subprocess.run
    observed = []

    def stop_after_copy(arguments, **kwargs):
        if any(str(arg).endswith("check-python.py") for arg in arguments):
            copied = Path(kwargs["cwd"]) / CONTRACT
            assert copied.read_bytes() == (ROOT / CONTRACT).read_bytes()
            observed.append(copied)
            return subprocess.CompletedProcess(arguments, 1, "", "test stopped after input copy")
        return original_run(arguments, **kwargs)

    monkeypatch.setattr(subprocess, "run", stop_after_copy)
    monkeypatch.setattr(sys, "argv", ["qualify.py", str(tmp_path)])
    with pytest.raises(SystemExit):
        namespace["main"]()
    assert len(observed) == 1
    report = json.loads((tmp_path / "installed.json").read_text())
    assert report["source_inputs"][CONTRACT] == hashlib.sha256((ROOT / CONTRACT).read_bytes()).hexdigest()
    assert report["temporary_workspace_removed"]


def test_native_receipt_fingerprints_transfer_contract_before_allocation(monkeypatch, tmp_path):
    main = runpy.run_path(str(ROOT / "database/tempo_learning_database/tooling/run_postgres.py"))["main"]
    calls = []

    def refuse_allocation(*arguments):
        calls.append(arguments)
        raise RuntimeError("test stopped before Docker allocation")

    monkeypatch.setitem(main.__globals__, "docker", refuse_allocation)
    monkeypatch.setattr(sys, "argv", ["run_postgres.py"])
    with pytest.raises(SystemExit):
        main(test_paths=[Path("services/learning/runtime/tests/test_installation.py")],
             fixture_profile="essential", evidence_directory=tmp_path)
    assert len(calls) == 1 and calls[0][:2] == ("image", "inspect")
    report = json.loads((tmp_path / "native.json").read_text())
    assert report["dependency_inputs"][CONTRACT] == hashlib.sha256((ROOT / CONTRACT).read_bytes()).hexdigest()
    assert report["complete"] is False
