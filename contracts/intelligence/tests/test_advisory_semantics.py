import json
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from tempo_intelligence_contracts import v1
from .conftest import load_fixture

ROOT = Path(__file__).resolve().parents[1]


def publication_form(publication, wrapped):
    if wrapped:
        payload = load_fixture("available")
        payload["publication"] = publication
        return v1.IntelligenceAvailability, payload
    return v1.AdvisoryPublication, publication


@pytest.mark.parametrize("wrapped", [False, True], ids=["standalone", "wrapped"])
@pytest.mark.parametrize("kind", ["ranking", "probabilities", "readiness", "coverage"])
def test_standalone_and_nested_advisory_valid_controls(kind, wrapped):
    model, payload = publication_form(load_fixture("advice-" + kind), wrapped)
    assert json.loads(v1.serialize_contract(model.model_validate(payload))) == payload


@pytest.mark.parametrize("wrapped", [False, True], ids=["standalone", "wrapped"])
@pytest.mark.parametrize(
    "expiry,accepted",
    [
        ("2026-10-14T12:00:00.000002Z", True),
        ("2026-10-14T12:00:00.000001Z", False),
        ("2026-10-14T12:00:00.000000Z", False),
        ("2026-10-14T14:00:00.000001+02:00", False),
    ],
    ids=[
        "one_microsecond_later",
        "same_instant",
        "one_microsecond_earlier",
        "offset_equivalent_instant",
    ],
)
def test_advisory_microsecond_expiry_and_offset_equivalence(expiry, accepted, wrapped):
    publication = load_fixture("advice-ranking")
    publication["produced_at"] = "2026-10-14T12:00:00.000001Z"
    publication["valid_until"] = expiry
    model, payload = publication_form(publication, wrapped)
    schema = json.loads(
        (ROOT / "schemas/v1" / (model.__name__ + ".schema.json")).read_text()
    )
    Draft202012Validator(schema).validate(payload)
    if not accepted:
        with pytest.raises(ValidationError, match="expiry must follow"):
            model.model_validate(payload)
    else:
        assert (
            json.loads(v1.serialize_contract(model.model_validate(payload))) == payload
        )


def test_advisory_equivalent_timestamp_spelling_remains_distinct_serialized_payload():
    first = load_fixture("advice-ranking")
    second = load_fixture("advice-ranking")
    first["produced_at"] = "2026-10-14T12:00:00.000001Z"
    second["produced_at"] = "2026-10-14T14:00:00.000001+02:00"
    assert v1.serialize_contract(
        v1.AdvisoryPublication.model_validate(first)
    ) != v1.serialize_contract(v1.AdvisoryPublication.model_validate(second))


@pytest.mark.parametrize("wrapped", [False, True], ids=["standalone", "wrapped"])
@pytest.mark.parametrize("kind", ["probabilities", "coverage"])
@pytest.mark.parametrize(
    "deviation,accepted",
    [(0.5e-9, True), (-0.5e-9, True), (2e-9, False)],
    ids=["within_positive_tolerance", "within_negative_tolerance", "outside_tolerance"],
)
def test_advisory_probability_and_coverage_mass_tolerance(
    kind, deviation, accepted, wrapped
):
    publication = load_fixture("advice-" + kind)
    if kind == "probabilities":
        publication["advisory"]["moves"] = [{"move": "e2e4", "probability": 0.2}]
        publication["advisory"]["unknown_mass"] = 0.8 + deviation
    else:
        publication["advisory"]["unknown_mass"] = 0.5 + deviation
    model, payload = publication_form(publication, wrapped)
    schema = json.loads(
        (ROOT / "schemas/v1" / (model.__name__ + ".schema.json")).read_text()
    )
    Draft202012Validator(schema).validate(payload)
    if accepted:
        model.model_validate(payload)
    else:
        with pytest.raises(ValidationError, match="mass must total one"):
            model.model_validate(payload)
