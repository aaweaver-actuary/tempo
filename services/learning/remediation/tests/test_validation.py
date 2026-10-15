from dataclasses import replace
from datetime import datetime, timezone
import json
import pytest
from tempo_game_evidence_contracts import v1, v2
from tempo_learning_remediation import RemediationError, validate_delivery
from .support import DECISION, context, delivery


@pytest.mark.parametrize("contract", [1,2])
def test_supported_v1_v2_miss_links_to_trusted_current_definition(contract):
    value = delivery(contract=contract)
    result = validate_delivery(value,context(value))
    assert len(result) == 1 and result[0].card_id == "card"
    assert (result[0].occurred_at is None) is (contract == 1)


@pytest.mark.parametrize("change,reason", [
    ({"workspace_id":"forged"},"source_authority_mismatch"),
    ({"game_id":"forged"},"source_authority_mismatch"),
    ({"evidence_id":"forged"},"source_authority_mismatch"),
    ({"game_revision":2},"source_revision_mismatch"),
    ({"evidence_revision":2},"source_revision_mismatch"),
])
def test_forged_workspace_game_and_evidence_references_are_rejected(change, reason):
    value = delivery()
    with pytest.raises(RemediationError,match=reason):
        validate_delivery(value.model_copy(update=change),context(value))


@pytest.mark.parametrize("accepted,reason", [
    (None,"publication_not_active"),
    (replace(DECISION,active=False),"publication_not_active"),
    (replace(DECISION,card_revision=2),"accepted_definition_mismatch"),
    (replace(DECISION,publication_id="other"),"accepted_definition_mismatch"),
    (replace(DECISION,publication_generation=2),"accepted_definition_mismatch"),
    (replace(DECISION,decision_id="other"),"accepted_definition_mismatch"),
    (replace(DECISION,position_key="other"),"unsupported_decision_linkage"),
    (replace(DECISION,accepted_moves=("e2e4",)),"unsupported_decision_linkage"),
    (replace(DECISION,workspace_id="foreign"),"accepted_workspace_mismatch"),
    (replace(DECISION,card_id="unrelated",aliases=()),"unsupported_card_linkage"),
])
def test_stale_or_incoherent_accepted_content_never_grants_permission(accepted,reason):
    value = delivery()
    with pytest.raises(RemediationError,match=reason):
        validate_delivery(value,context(value,accepted=accepted))


@pytest.mark.parametrize("observed",["e2e4","c2c4"])
def test_valid_alternative_is_not_a_miss_even_when_every_wire_reference_is_valid(observed):
    value = delivery()
    data = value.model_dump(mode="json"); data["encounters"][0]["observed_move"]=observed
    forged = v2.EvidenceDelivery.model_validate(data)
    with pytest.raises(RemediationError,match="incoherent_comparison"):
        validate_delivery(forged,context(forged))


def test_v1_requires_unambiguous_actual_miss_provenance():
    value = delivery(contract=1)
    missing = value.model_copy(update={"misses":[]})
    with pytest.raises(RemediationError,match="unsupported_miss_provenance"):
        validate_delivery(missing,context(missing))
    ambiguous=value.model_copy(update={"comparisons":value.comparisons*2})
    with pytest.raises(RemediationError,match="ambiguous_obligation_linkage"):
        validate_delivery(ambiguous,context(ambiguous))


def test_transport_and_route_aliases_keep_same_logical_incident():
    value=delivery()
    alias=delivery(card="route-alias",transport="retry",obligation="alias")
    first=validate_delivery(value,context(value))[0]
    second=validate_delivery(alias,context(alias))[0]
    assert first.incident_id == second.incident_id
    assert json.loads(first.payload_json)["obligation"]["card_id"] == "card"
    assert json.loads(second.payload_json)["obligation"]["card_id"] == "route-alias"


def test_v1_oversized_batch_and_future_occurrence_are_explicit_failures():
    value=delivery(contract=1)
    with pytest.raises(RemediationError,match="batch_limit_exceeded"):
        validate_delivery(value.model_copy(update={"obligations":value.obligations*129}),context(value))
    future=delivery(occurred_at="2027-01-01T00:00:00Z")
    with pytest.raises(RemediationError,match="occurrence_in_future"):
        validate_delivery(future,context(future))


@pytest.mark.parametrize("occurred_at", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"])
def test_wire_valid_occurrence_utc_overflow_is_an_explicit_terminal_failure(occurred_at):
    value = delivery(occurred_at=occurred_at)
    assert value.occurred_at == occurred_at
    with pytest.raises(RemediationError) as failure:
        validate_delivery(value, context(value))
    assert failure.value.status == "terminal_failure"
    assert failure.value.reason == "occurrence_out_of_range"


def test_normal_offset_occurrence_preserves_its_utc_instant():
    value = delivery(occurred_at="2026-10-14T13:00:00+01:00")
    contribution, = validate_delivery(value, context(value))
    assert contribution.occurred_at == datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    assert contribution.occurred_at.tzinfo is timezone.utc


@pytest.mark.parametrize("received_at,error", [
    (datetime(2026, 10, 15), ValueError),
    (datetime.fromisoformat("0001-01-01T00:00:00+01:00"), OverflowError),
])
def test_invalid_trusted_receive_time_is_not_reclassified_as_a_producer_failure(received_at, error):
    value = delivery()
    with pytest.raises(error):
        validate_delivery(value, context(value, received_at=received_at))
