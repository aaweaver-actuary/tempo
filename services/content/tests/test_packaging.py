"""The regular suite also runs unchanged in source-free installed consumers."""
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import tempo_content


def test_installed_distribution_declares_independent_runtime_and_cli():
    distribution = importlib.metadata.distribution("tempo-content")
    assert distribution.version == "0.1.0"
    assert set(distribution.metadata["Requires-Python"].split(",")) == {">=3.12", "<3.15"}
    assert "site-packages" in Path(tempo_content.__file__).parts
    dependencies = distribution.requires or []
    assert not any(any(forbidden in requirement.lower() for forbidden in
                       ("tempo-learning", "game-evidence", "redis", "celery", "maia", "stockfish", "fastapi"))
                   for requirement in dependencies)
    assert any(entry.name == "tempo-content" and entry.value == "tempo_content.cli:main"
               for entry in distribution.entry_points)


def test_packaged_migrations_have_complete_manifest_and_checksums():
    import hashlib
    directory = Path(tempo_content.__file__).parent / "migrations"
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest
    identities = set()
    for entry in manifest:
        assert entry["id"] not in identities
        assert set(entry.get("depends_on", [])) <= identities
        source = directory / entry["path"]
        assert source.resolve().is_relative_to(directory.resolve())
        assert source.suffix == ".sql" and source.read_bytes()
        assert len(hashlib.sha256(source.read_bytes()).hexdigest()) == 64
        identities.add(entry["id"])


def test_import_and_cli_help_do_not_initialize_services():
    environment = {name: value for name, value in os.environ.items()
                   if not name.startswith(("PYTHON", "PG", "TEMPO_", "CELERY_"))}
    guard = '''
import sys
from importlib.abc import MetaPathFinder
class Guard(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'app', 'backend', 'tempo_learning_runtime', 'tempo_game_evidence', 'redis', 'celery', 'maia', 'stockfish'}:
            raise AssertionError('Forbidden service import: ' + fullname)
sys.meta_path.insert(0, Guard())
def reject_services(event, arguments):
    if event in {'socket.connect', 'socket.bind'}:
        raise AssertionError('Unexpected connection during import/help')
sys.addaudithook(reject_services)
import tempo_content
from tempo_content.cli import main
raise SystemExit(main(['--help']))
'''
    result = subprocess.run([sys.executable, "-I", "-c", guard], env=environment,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    for command in ("readiness", "migrate", "source", "inventory", "publication"):
        assert command in result.stdout


def qualification_module():
    source = Path(__file__).resolve().parents[1] / "run_suite.py"
    specification = importlib.util.spec_from_file_location("content_qualification", source)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


@pytest.mark.parametrize("defect", ["empty", "duplicate", "deselected", "collection_error", "missing_phase", "skipped", "junit_missing", "changed_identity"])
def test_qualification_rejects_incomplete_or_nonpassing_inventory(tmp_path, defect):
    from copy import deepcopy
    collection = {"exit_code": 0, "collected": ["tests/test_example.py::test_example"],
                  "deselected": [], "collection_errors": [], "reports": []}
    execution = deepcopy(collection)
    execution["reports"] = [{"nodeid": collection["collected"][0], "phase": phase, "outcome": "passed"}
                            for phase in ("setup", "call", "teardown")]
    junit = tmp_path / "cases.xml"
    junit.write_text('<testsuites><testsuite><testcase classname="tests.test_example" name="test_example"/></testsuite></testsuites>')
    if defect == "empty":
        collection["collected"] = []
    elif defect == "duplicate":
        collection["collected"] *= 2
    elif defect == "deselected":
        execution["deselected"] = ["another_test"]
    elif defect == "collection_error":
        collection["collection_errors"] = ["collection failed"]
    elif defect == "missing_phase":
        execution["reports"].pop()
    elif defect == "skipped":
        execution["reports"][1]["outcome"] = "skipped"
    elif defect == "junit_missing":
        junit.write_text("<testsuites/>")
    else:
        execution["collected"] = ["tests/test_example.py::different_test"]
    with pytest.raises(RuntimeError):
        qualification_module().validate_inventory(collection, execution, junit)


def test_qualification_rejects_inherited_test_selection():
    module = qualification_module()
    with pytest.raises(ValueError):
        module.junit_destination("-k only_one_test --junitxml=result.xml", allow_junit=True)
    with pytest.raises(ValueError):
        module.junit_destination("--junitxml=first.xml --junitxml=second.xml", allow_junit=True)
