"""Regression for overlapping declared inputs in consumer qualification."""
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "integration/qualify_artifacts.py"
SPEC = importlib.util.spec_from_file_location("content_qualify_artifacts", SCRIPT)
qualifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qualifier)


def test_consumer_preparation_preserves_overlaps_and_artifactless_evidence_math(tmp_path, monkeypatch):
    root = tmp_path / "source"
    runtime = root / "services/learning/runtime"
    runtime.mkdir(parents=True)
    (runtime / "pyproject.toml").write_text('[tool.uv.sources]\ntempo-evidence-math = { path = "../../../packages/evidence-math" }\n')
    evidence = root / "packages/evidence-math"
    (evidence / "src").mkdir(parents=True)
    (evidence / "pyproject.toml").write_text('[project]\nname = "tempo-evidence-math"\n')
    (evidence / "src/input.py").write_bytes(b"evidence input\n")
    database = root / "database/tempo_learning_database"
    (database / "tooling").mkdir(parents=True)
    (database / "root.txt").write_bytes(b"database root\n")
    (database / "tooling/runner.txt").write_bytes(b"runner input\n")
    components = [
        {"id": "learning", "root": "services/learning/runtime", "artifact": {"name": "learning"},
         "dependencies": ["tempo_evidence_math"], "buildInputs": ["pyproject.toml", "../../../database/tempo_learning_database", "../../../database/tempo_learning_database/tooling"]},
        {"id": "evidence-math", "root": "packages/evidence-math", "artifact": None,
         "dependencies": [], "buildInputs": ["pyproject.toml", "src"]},
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
        if label == "consumer-build-evidence-math":
            assert (cwd / "packages/evidence-math/src/input.py").read_bytes() == b"evidence input\n"
            raise PreparationComplete
        copied = cwd / "database/tempo_learning_database"
        assert (copied / "root.txt").read_bytes() == b"database root\n"
        assert (copied / "tooling/runner.txt").read_bytes() == b"runner input\n"

    with pytest.raises(PreparationComplete):
        qualifier.qualify_consumers(workspace, destination, check_prepared, {}, "unused", tmp_path)
