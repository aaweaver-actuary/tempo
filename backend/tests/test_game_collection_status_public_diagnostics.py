"""Public collection status emits only established, bounded diagnostic codes."""

import json

import pytest

from app.product_composition.game_collection_status import _encode


INTEGRITY_REASONS = (
    "integrity_graph_not_ready",
    "integrity_graph_generation_mismatch",
    "integrity_state_missing",
    "integrity_scan_incomplete",
    "integrity_certificate_invalid",
    "legacy_completed_current_integrity_required",
)


def _report(reason):
    # These are the two read projections of a persisted, held delivery reason.
    return {
        "identity": {},
        "delivery": {"qualification_reasons": [{"diagnostic": reason, "records": 1}]},
        "selected_game": {"receipts": [{
            "operation_id": "private-operation-123",
            "delivery_state": "pending",
            "delivery_qualification": {"status": "held", "diagnostic": reason},
        }]},
    }


@pytest.mark.parametrize("reason", INTEGRITY_REASONS)
def test_public_integrity_hold_preserves_exact_persisted_reason_in_selected_and_aggregate(reason):
    public = json.loads(_encode(_report(reason)))
    assert public["delivery"]["qualification_reasons"] == [{"diagnostic": reason, "records": 1}]
    selected, = public["selected_game"]["receipts"]
    assert selected["delivery_qualification"] == {"status": "held", "diagnostic": reason}
    assert selected["operation_id"].startswith("redacted:")
    assert "private-operation-123" not in json.dumps(public)


def test_public_identity_mismatch_appears_only_on_selected_read():
    report = _report("integrity_state_missing")
    report["selected_game"]["receipts"][0].update(
        delivery_state="acknowledged", diagnostic="owner_receipt_identity_mismatch", receipt=None)
    public = json.loads(_encode(report))
    selected, = public["selected_game"]["receipts"]
    assert selected["diagnostic"] == "owner_receipt_identity_mismatch"
    assert selected["delivery_state"] == "acknowledged" and selected["receipt"] is None
    assert public["delivery"]["qualification_reasons"] == [
        {"diagnostic": "integrity_state_missing", "records": 1}]


@pytest.mark.parametrize("unsafe", [
    "integrity_graph_not_ready:private-account-123",
    "integrity_certificate_invalid_extra",
    "owner_receipt_identity_mismatch:private-operation-123",
    "private-account-123",
    "RuntimeError('private-account-123')",
])
def test_public_integrity_hold_redacts_unknown_and_lookalike_reasons_in_both_outputs(unsafe):
    public = json.loads(_encode(_report(unsafe)))
    assert public["delivery"]["qualification_reasons"] == [
        {"diagnostic": "redacted_diagnostic", "records": 1}]
    selected, = public["selected_game"]["receipts"]
    assert selected["delivery_qualification"]["diagnostic"] == "redacted_diagnostic"
    assert unsafe not in json.dumps(public)
