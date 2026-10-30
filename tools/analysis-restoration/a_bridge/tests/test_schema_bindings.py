"""The documented consumer schemas accept projected physical bindings."""
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

SCHEMAS = Path(__file__).resolve().parents[1] / 'fixtures' / 'schemas'
NAMES = ('learning-publication-bundle-v2.json', 'comparison-snapshot-v2.json')


def binding_schema(name):
    document=json.loads((SCHEMAS/name).read_text())
    Draft202012Validator.check_schema(document)
    return {'$defs':document['$defs'], '$ref':'#/$defs/Binding'}


@pytest.mark.parametrize('name', NAMES)
def test_C3_documented_schema_accepts_nonzero_projected_binding(name):
    projected={'card_id':'physical-card','ply':7,'reentry':False,
        'exercise_ply':2,'decision_id':'explicit-interior-decision'}
    Draft202012Validator(binding_schema(name)).validate(projected)


def test_C3_documented_binding_schemas_have_matching_projection_contract():
    schemas=[json.loads((SCHEMAS/name).read_text())['$defs']['Binding'] for name in NAMES]
    for schema in schemas:
        assert set(schema['properties'])=={'card_id','ply','reentry','exercise_ply','decision_id'}
    for name in ('exercise_ply','decision_id'):
        assert schemas[0]['properties'][name]==schemas[1]['properties'][name]


@pytest.mark.parametrize('name', NAMES)
@pytest.mark.parametrize('change', [{'exercise_ply':40}, {'exercise_ply':-1}, {'decision_id':''}, {'unexpected':True}])
def test_C3_documented_binding_schemas_retain_strict_projection_bounds(name,change):
    payload={'card_id':'physical-card','ply':7,'exercise_ply':2,'decision_id':'explicit-interior-decision',**change}
    assert list(Draft202012Validator(binding_schema(name)).iter_errors(payload))


def test_endgames_retirement_schema_allows_only_null_compatibility_metadata():
    document = json.loads((SCHEMAS / "learning-publication-bundle-v2.json").read_text())
    Draft202012Validator.check_schema(document)
    facts = document["$defs"]["ExecutionFacts"]
    assert facts["properties"]["endgame"] == {"default": None, "title": "Endgame", "type": "null"}
    assert "endgame" not in facts["required"]
    assert "endgame" not in facts["properties"]["content_type"]["enum"]
    assert facts["additionalProperties"] is False
    validator = Draft202012Validator({"$defs": document["$defs"], "$ref": "#/$defs/ExecutionFacts"})
    ordinary = {"card_id": "ordinary-card", "content_type": "opening", "due_date": "2026-10-29", "endgame": None}
    validator.validate(ordinary)
    validator.validate({key: value for key, value in ordinary.items() if key != "endgame"})
    for change in ({"endgame": {}}, {"content_type": "endgame"}, {"unexpected": True}):
        assert list(validator.iter_errors({**ordinary, **change}))
