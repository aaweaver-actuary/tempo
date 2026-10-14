"""Owning suite: installed models, fixed examples and exported schema parity."""

import importlib
import json
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from tempo_game_evidence_contracts import v1

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = sorted((PACKAGE_ROOT / "fixtures/v1").glob("*.json"))
assert FIXTURES, "zero contract fixtures"


@pytest.mark.parametrize("fixture_path", FIXTURES, ids=lambda path: path.stem)
def test_independently_authored_producer_round_trip(fixture_path):
    fixture = json.loads(fixture_path.read_text())
    model = getattr(v1, fixture["model"])
    value = v1.validate_contract(model, json.dumps(fixture["payload"]))
    assert json.loads(v1.serialize_contract(value)) == fixture["payload"]
    schema = json.loads(
        (PACKAGE_ROOT / "schemas/v1" / (model.__name__ + ".schema.json")).read_text()
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(fixture["payload"])


@pytest.mark.parametrize("model", v1.PUBLIC_MODELS, ids=lambda model: model.__name__)
def test_exported_json_schema_matches_authoritative_model(model):
    expected = model.model_json_schema()
    expected["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    expected["$id"] = (
        "https://tempo.invalid/contracts/game-evidence/v1/"
        + model.__name__
        + ".schema.json"
    )
    committed = json.loads(
        (PACKAGE_ROOT / "schemas/v1" / (model.__name__ + ".schema.json")).read_text()
    )
    assert committed == expected


@pytest.mark.parametrize("fixture_path", FIXTURES, ids=lambda path: path.stem)
def test_future_versions_and_unknown_fields_are_rejected(fixture_path):
    fixture = json.loads(fixture_path.read_text())
    model = getattr(v1, fixture["model"])
    for change in (
        {"contract_version": 2},
        {"contract_version": True},
        {"contract_version": "1"},
        {"internal_orm_record": {}},
    ):
        with pytest.raises(ValidationError):
            v1.validate_contract(model, json.dumps({**fixture["payload"], **change}))
    malformed = dict(fixture["payload"])
    del malformed["workspace_id"]
    with pytest.raises(ValidationError):
        v1.validate_contract(model, json.dumps(malformed))


def test_every_public_interface_has_a_producer_example():
    represented = {json.loads(path.read_text())["model"] for path in FIXTURES}
    assert represented == {model.__name__ for model in v1.PUBLIC_MODELS}


def test_imports_resolve_to_installed_artifact():
    installed = Path(
        importlib.import_module("tempo_game_evidence_contracts").__file__
    ).resolve()
    assert "site-packages" in installed.parts
    assert PACKAGE_ROOT not in installed.parents


def test_non_json_numbers_and_coercions_are_rejected():
    fixture = json.loads(FIXTURES[0].read_text())
    model = getattr(v1, fixture["model"])
    for change in (
        {"workspace_id": 42},
        {"workspace_id": ""},
        {"internal_number": 2**53},
    ):
        with pytest.raises(ValidationError):
            model.model_validate({**fixture["payload"], **change})
