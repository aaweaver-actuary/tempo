"""Installed artifact boundary proof; no runtime source path is admitted."""
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys

from tempo_learning_transfer_contracts import v1
import tempo_learning_transfer_contracts


def test_installed_public_import_has_no_runtime_database_or_provider_side_effects(tmp_path):
    script = """
import importlib.metadata
from pathlib import Path
import sys
from tempo_learning_transfer_contracts import v1
import tempo_learning_transfer_contracts as package
assert importlib.metadata.version('tempo-learning-transfer-contracts') == '1.0.0'
assert package.__version__ == '1.0.0'
assert 'site-packages' in Path(package.__file__).resolve().parts
assert Path(package.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
for forbidden in ('tempo_learning_runtime', 'tempo_learning_database', 'backend',
                  'app', 'services', 'celery', 'psycopg', 'psycopg_pool', 'torch',
                  'numpy', 'onnxruntime', 'fastapi', 'chess', 'fsrs'):
    assert not any(name == forbidden or name.startswith(forbidden + '.')
                   for name in sys.modules), forbidden
assert len(v1.PUBLIC_MODELS) == 7
print('installed independent v1 imports passed')
"""
    result = subprocess.run([sys.executable, "-I", "-c", script], cwd=tmp_path,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_installed_schema_resources_match_unchanged_public_models():
    installed = Path(tempo_learning_transfer_contracts.__file__).resolve()
    assert "site-packages" in installed.parts
    assert importlib.metadata.requires("tempo-learning-transfer-contracts") == ["pydantic==2.12.5"]
    for model in v1.PUBLIC_MODELS:
        expected = model.model_json_schema()
        expected["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        expected["$id"] = (
            f"https://tempo.invalid/contracts/learning-transfer/v1/{model.__name__}.schema.json"
        )
        schema = installed.parent / "schemas/v1" / (model.__name__ + ".schema.json")
        assert json.loads(schema.read_text()) == expected


def test_reviewed_golden_manifests_keep_original_envelopes_and_dispositions():
    root = Path(__file__).resolve().parents[1]
    fixtures = sorted((root / "fixtures/v1").glob("*.json"))
    assert len(fixtures) == 2
    for path in fixtures:
        fixture = json.loads(path.read_text())
        value = v1.validate_contract(v1.SourceManifest, json.dumps(fixture["payload"]))
        assert json.loads(v1.serialize_contract(value)) == fixture["payload"]
        assert value.scope == "representative"
        plan = v1.plan_transfer(value)
        expected = "BLOCK" if "blocked" in path.stem else "staging_only"
        assert plan.status == expected
