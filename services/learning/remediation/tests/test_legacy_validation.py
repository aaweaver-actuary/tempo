from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from tempo_game_evidence_contracts import v3
from tempo_learning_remediation import (LegacyAcceptedDecision, ReceiveContext, RemediationError,
                                        accepted_storage_identity, validate_delivery)
from .support import DECISION


def delivery():
    return v3.EvidenceDelivery.model_validate_json((Path(__file__).parent / "legacy-delivery.json").read_bytes())


def decision(value=None):
    value = value or delivery()
    comparison = value.comparisons[0]
    return LegacyAcceptedDecision(comparison.card_id, comparison.card_revision, comparison.decision_id,
        value.authority, value.encounters[0].position_key, ("a7a6",), value.workspace_id,
        comparison.route_id, comparison.route_ply, comparison.exercise_ply,
        comparison.executable_digest, ("a7a6",), True, True, True)


def context(value, current=None):
    class Authority:
        def validate_legacy_source(self, delivery): pass
        def legacy_decision(self, *args):
            return current if current is not None else decision(value)
    return ReceiveContext(value.workspace_id, value.game_id, value.game_revision, value.evidence_id,
        value.evidence_revision, datetime(2026, 10, 19, tzinfo=timezone.utc), Authority())


def test_typed_legacy_decision_uses_shared_contribution_without_publication_or_activation():
    value = delivery()
    contributions = validate_delivery(value, context(value))
    assert len(contributions) == 1
    result = contributions[0]
    assert result.card_id == "physical-black" and result.card_revision == 1
    assert (result.publication_id, result.publication_generation) == accepted_storage_identity(decision(value))
    assert result.publication_id.startswith("legacy-graph:")
    payload = json.loads(result.payload_json)
    assert payload["authority"]["kind"] == "legacy_graph"
    assert payload["game_source"]["raw_source_digest"] != payload["game_source"]["content_hash"]
    assert "publication" not in payload and "activation" not in payload


@pytest.mark.parametrize("change,reason", [
    (dict(physical_binding_complete=False), "unknown_is_not_permission"),
    (dict(authored_moves=("a7a6", "g8f6")), "unknown_is_not_permission"),
    (dict(card_revision=2), "accepted_definition_mismatch"),
    (dict(route_id="other"), "legacy_binding_authority_changed"),
    (dict(route_ply=7), "legacy_binding_authority_changed"),
    (dict(exercise_ply=3), "legacy_binding_authority_changed"),
    (dict(executable_digest="0" * 64), "legacy_binding_authority_changed"),
    (dict(accepted_moves=("g8f6",), authored_moves=("g8f6",)), "unsupported_decision_linkage"),
    (dict(workspace_id="other"), "accepted_workspace_mismatch"),
    (dict(active=False), "legacy_authority_not_current"),
    (dict(introduced=False), "legacy_card_not_introduced"),
    (dict(introduced_before_game=False), "legacy_introduction_chronology_unknown"),
    (dict(aliases=("alias",)), "legacy_physical_identity_required"),
])
def test_legacy_semantic_admission_fails_closed_without_duplicate_policy(change, reason):
    value = delivery()
    with pytest.raises(RemediationError) as error:
        validate_delivery(value, context(value, replace(decision(value), **change)))
    assert error.value.reason == reason


def test_legacy_cannot_use_a_content_decision_or_changed_authority():
    value = delivery()
    with pytest.raises(RemediationError, match="legacy_authority_variant_required"):
        validate_delivery(value, context(value, DECISION))
    changed = replace(decision(value), authority=value.authority.model_copy(update={"graph_generation": 8}))
    with pytest.raises(RemediationError, match="legacy_binding_authority_changed"):
        validate_delivery(value, context(value, changed))


def test_withdrawal_only_legacy_delivery_still_requires_its_trusted_source_scope():
    value = delivery().model_dump(mode="json")
    for key in ("encounters", "comparisons", "misses", "obligations"): value[key] = []
    value["withdrawals"] = [dict(obligation_id="legacy-obligation", expected_obligation_revision=1,
                                 reason="source_corrected")]
    value = v3.EvidenceDelivery.model_validate(value)
    class RefusedAuthority:
        def validate_legacy_source(self, delivery):
            raise RemediationError("terminal_failure", "legacy_withdrawal_scope_mismatch")
        def legacy_decision(self, *args): pytest.fail("withdrawal required a decision")
    with pytest.raises(RemediationError, match="legacy_withdrawal_scope_mismatch"):
        validate_delivery(value, replace(context(value), content=RefusedAuthority()))


def test_legacy_capture_utc_overflow_is_a_terminal_failure():
    value = delivery().model_copy(update={"captured_at": "0001-01-01T00:00:00+00:01"})
    with pytest.raises(RemediationError, match="legacy_capture_out_of_range"):
        validate_delivery(value, context(value))
