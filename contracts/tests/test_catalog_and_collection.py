import importlib
import json
from pathlib import Path
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_catalog_covers_every_versioned_public_interface_and_export():
    catalog = json.loads((ROOT / "catalog.json").read_text())
    assert len(catalog["contracts"]) == 5
    for entry in catalog["contracts"]:
        public = importlib.import_module(entry["public_namespace"])
        assert entry["models"] == [model.__name__ for model in public.PUBLIC_MODELS]
        metadata = tomllib.loads((ROOT / entry["root"] / "pyproject.toml").read_text())
        assert entry["artifact_version"] == metadata["project"]["version"]
        for version in entry.get("supported_wire_versions", [entry["wire_version"]]):
            namespace = entry["public_namespace"].rsplit(".", 1)[0] + f".v{version}"
            versioned = importlib.import_module(namespace)
            for model in versioned.PUBLIC_MODELS:
                assert f"schemas/v{version}/{model.__name__}.schema.json" in entry["schema_exports"]
        for export in entry["schema_exports"]:
            assert (ROOT / entry["root"] / export).is_file()
        assert entry["runtime_dependencies"] == ["pydantic==2.12.5"]


def test_zero_selected_contract_tests_fail_qualification(tmp_path):
    (tmp_path / "test_empty.py").write_text("def test_proof():\n    assert True\n")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(tmp_path / "test_empty.py"),
            "-k",
            "nonexistent_case",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 5, result.stdout + result.stderr


def test_skipped_selected_contract_test_fails_qualification(tmp_path):
    (tmp_path / "conftest.py").write_text((ROOT / "conftest.py").read_text())
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = -p no:cacheprovider\n")
    (tmp_path / "test_skipped.py").write_text(
        "import pytest\ndef test_proof():\n    pytest.skip('deliberate rejection fixture')\n"
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(tmp_path / "test_skipped.py"), "-q"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stdout + result.stderr
