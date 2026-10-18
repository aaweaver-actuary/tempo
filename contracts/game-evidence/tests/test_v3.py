"""Legacy wire is explicit; v1/v2 cannot silently acquire observed authority."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from tempo_game_evidence_contracts import v1, v2, v3

ROOT = Path(__file__).resolve().parents[1]


def delivery():
    return json.loads((ROOT / "fixtures/v3/delivery.json").read_text())["payload"]


@pytest.mark.parametrize("name", ["delivery", "completed"])
def test_v3_independent_fixture_round_trip_and_schema(name):
    from jsonschema import Draft202012Validator
    fixture = json.loads((ROOT / f"fixtures/v3/{name}.json").read_text())
    model = getattr(v3, fixture["model"])
    value = model.model_validate(fixture["payload"])
    assert json.loads(v3.serialize_contract(value)) == fixture["payload"]
    schema = json.loads((ROOT / f"schemas/v3/{model.__name__}.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(fixture["payload"])
    expected = model.model_json_schema()
    expected.update({"$schema": "https://json-schema.org/draft/2020-12/schema",
                     "$id": f"https://tempo.invalid/contracts/game-evidence/v3/{model.__name__}.schema.json"})
    assert schema == expected


@pytest.mark.parametrize("fault", ["publication", "activation", "workspace", "unready", "revision",
    "encounter", "miss", "unknown", "duplicate", "empty", "future_version", "boolean_version", "time_unknown"])
def test_v3_rejects_mixed_authority_incoherent_links_or_unsupported_scope(fault):
    value = delivery()
    if fault in {"publication", "activation"}: value[fault] = {}
    elif fault == "workspace": value["authority"]["workspace_id"] = "other"
    elif fault == "unready": value["authority"]["graph_state"] = "building"
    elif fault == "revision": value["comparisons"][0]["card_revision"] = 2
    elif fault == "encounter": value["obligations"][0]["encounter_id"] = "other"
    elif fault == "miss": value["obligations"][0]["miss_id"] = "other"
    elif fault == "unknown": value["comparisons"][0]["status"] = "unknown"
    elif fault == "duplicate": value["obligations"] *= 2
    elif fault == "empty": value.update(obligations=[], misses=[])
    elif fault == "future_version": value["contract_version"] = 4
    elif fault == "boolean_version": value["contract_version"] = True
    elif fault == "time_unknown": value["occurred_at"] = None
    with pytest.raises(ValidationError): v3.EvidenceDelivery.model_validate(value)


def test_v3_withdrawal_is_explicit_and_older_wires_reject_legacy_authority():
    value = delivery()
    for module in (v1, v2):
        with pytest.raises(ValidationError): module.EvidenceDelivery.model_validate(value)
    value.update(evidence_revision=2, encounters=[], comparisons=[], misses=[], obligations=[],
        withdrawals=[dict(obligation_id="legacy-obligation", expected_obligation_revision=1, reason="source_corrected")])
    assert v3.EvidenceDelivery.model_validate(value).withdrawals[0].expected_obligation_revision == 1


def test_legacy_storage_identity_has_no_capture_or_study_state():
    token = v3.EvidenceDelivery.model_validate(delivery()).authority
    key, generation = token.storage_identity()
    assert key.startswith("legacy-graph:") and generation == token.graph_generation
    raw = token.model_dump(mode="json")
    assert not ({"captured_at", "capture_digest", "reviews", "due_date", "fsrs_card_json"} & raw.keys())
    changed = token.model_copy(update={"scope_source_revision": token.scope_source_revision + 1})
    assert changed.storage_identity() != token.storage_identity()
