"""Regression for overlapping declared inputs in consumer qualification."""
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "integration/qualify_artifacts.py"
SPEC = importlib.util.spec_from_file_location("content_qualify_artifacts", SCRIPT)
qualifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qualifier)


def test_consumer_preparation_preserves_overlapping_declared_inputs(tmp_path, monkeypatch):
    root = tmp_path / "source"
    runtime = root / "services/learning/runtime"
    runtime.mkdir(parents=True)
    database = root / "database/tempo_learning_database"
    (database / "tooling").mkdir(parents=True)
    (database / "root.txt").write_bytes(b"database root\n")
    (database / "tooling/runner.txt").write_bytes(b"runner input\n")
    components = [
        {"id": "learning", "root": "services/learning/runtime", "artifact": {"name": "learning"},
         "dependencies": [], "buildInputs": ["../../../database/tempo_learning_database", "../../../database/tempo_learning_database/tooling"]},
        {"id": "game-evidence", "root": "services/game-evidence", "artifact": {"name": "game-evidence"},
         "dependencies": [], "buildInputs": []},
    ]
    (root / "components.json").write_text(json.dumps({"components": components}))
    monkeypatch.setattr(qualifier, "ROOT", root)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    destination = tmp_path / "evidence"
    destination.mkdir()

    class PreparationComplete(Exception):
        pass

    def check_prepared(arguments, cwd, label):
        copied = cwd / "database/tempo_learning_database"
        assert (copied / "root.txt").read_bytes() == b"database root\n"
        assert (copied / "tooling/runner.txt").read_bytes() == b"runner input\n"
        raise PreparationComplete

    with pytest.raises(PreparationComplete):
        qualifier.qualify_consumers(workspace, destination, check_prepared, {}, "unused", tmp_path)
