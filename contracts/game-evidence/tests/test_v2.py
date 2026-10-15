"""Independent v2 producer/consumer cases; v1 stays unchanged and additive."""
import json
from pathlib import Path
import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from tempo_game_evidence_contracts import v1, v2

ROOT=Path(__file__).resolve().parents[1]
FIXTURES=sorted((ROOT/"fixtures/v2").glob("*.json"))
assert FIXTURES


@pytest.mark.parametrize("path",FIXTURES,ids=lambda path:path.stem)
def test_v2_independently_authored_fixture_round_trip_and_exported_schema(path):
    fixture=json.loads(path.read_text())
    model=getattr(v2,fixture["model"])
    value=v2.validate_contract(model,json.dumps(fixture["payload"]))
    assert json.loads(v2.serialize_contract(value)) == fixture["payload"]
    schema=json.loads((ROOT/"schemas/v2"/(fixture["model"]+".schema.json")).read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(fixture["payload"])
    expected=model.model_json_schema()
    expected.update({"$schema":"https://json-schema.org/draft/2020-12/schema",
                     "$id":"https://tempo.invalid/contracts/game-evidence/v2/"+fixture["model"]+".schema.json"})
    assert schema == expected
    # Consumer-maintained decisions, not expectations generated from schema.
    if fixture["model"] == "EvidenceDelivery":
        if value.withdrawals:
            assert value.encounters == value.obligations == []
            assert value.withdrawals[0].expected_obligation_revision == 1
            assert value.withdrawals[0].reason == "source_corrected"
        else:
            obligation=value.obligations[0]
            assert obligation.miss_id == "miss-a" and obligation.encounter_id == "encounter-a"
            assert obligation.publication_id == "publication-a" and obligation.card_revision == 1
    elif fixture["model"] == "EvidenceAcknowledgement":
        assert (value.status == "completed") == (value.learning_revision is not None)


def base():
    return json.loads((ROOT/"fixtures/v2/delivery.json").read_text())["payload"]


@pytest.mark.parametrize("change",["wrong_encounter","wrong_miss","wrong_publication","wrong_decision","lineage","duplicate","implicit_empty","mixed_withdrawal","oversized"])
def test_v2_rejects_incoherent_links_and_unbounded_or_implicit_withdrawals(change):
    data=base()
    if change == "wrong_encounter": data["obligations"][0]["encounter_id"]="forged"
    if change == "wrong_miss": data["obligations"][0]["miss_id"]="forged"
    if change == "wrong_publication": data["obligations"][0]["publication_id"]="forged"
    if change == "wrong_decision": data["comparisons"][0]["decision_id"]="forged"
    if change == "lineage": data["obligations"][0]["source_revision"]=2
    if change == "duplicate": data["obligations"]*=2
    if change == "implicit_empty": data.update(encounters=[],comparisons=[],misses=[],obligations=[])
    if change == "mixed_withdrawal": data["withdrawals"]=[dict(obligation_id="obligation-a",expected_obligation_revision=1,reason="reanalysis")]
    if change == "oversized": data["obligations"]*=129
    with pytest.raises(ValidationError): v2.EvidenceDelivery.model_validate(data)


@pytest.mark.parametrize("version",[True,"2",1,3])
def test_v2_never_silently_negotiates_wire_version(version):
    with pytest.raises(ValidationError): v2.EvidenceDelivery.model_validate({**base(),"contract_version":version})


def test_v1_rejects_v2_and_v2_requires_explicit_chronology_and_linkage():
    with pytest.raises(ValidationError): v1.EvidenceDelivery.model_validate(base())
    old=json.loads((ROOT/"fixtures/v1/delivery.json").read_text())["payload"]
    with pytest.raises(ValidationError): v2.EvidenceDelivery.model_validate({**old,"contract_version":2})
