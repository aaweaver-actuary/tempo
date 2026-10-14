import importlib
import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from jsonschema import Draft202012Validator

FIXTURES = sorted(
    (Path(__file__).resolve().parents[1] / "semantic-negative/v1").glob("*.json")
)
assert FIXTURES, "zero semantic negative fixtures"


@pytest.mark.parametrize("path", FIXTURES, ids=lambda path: path.stem)
def test_independent_python_and_typescript_consumers_reject_semantic_conflicts(path):
    fixture = json.loads(path.read_text())
    public = importlib.import_module(
        "tempo_" + fixture["package"].replace("-", "_") + "_contracts.v1"
    )
    schema_path = (
        Path(__file__).resolve().parents[1]
        / fixture["package"]
        / "schemas/v1"
        / (fixture["model"] + ".schema.json")
    )
    Draft202012Validator(json.loads(schema_path.read_text())).validate(
        fixture["payload"]
    )
    with pytest.raises(ValidationError):
        public.validate_contract(
            getattr(public, fixture["model"]), json.dumps(fixture["payload"])
        )
