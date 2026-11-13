"""Finite Study issuance decisions; these do not prove storage transactions."""

from app.study.next_exercise import issue_returned_head


PAYLOAD = {"queue_date": "2026-11-02", "entry_id": 4, "card_id": "card", "expected_revision": 2}


def test_preissued_replay_avoids_dispatch():
    calls = []
    outcome, result = issue_returned_head(
        PAYLOAD, already_issued=lambda p: True,
        replay=lambda p: ("replay", {"plan_id": "original"}, None),
        dispatch=lambda p: calls.append(p),
    )
    assert outcome is None
    assert result["position_presentation"] == {"plan_id": "original"}
    assert calls == []


def test_pending_preserves_original_operation_without_replay():
    pending = {"kind": "pending", "operation_id": "original-operation"}
    calls = []
    outcome, result = issue_returned_head(
        PAYLOAD, already_issued=lambda p: False,
        replay=lambda p: calls.append(p), dispatch=lambda p: (pending, None),
    )
    assert outcome == pending
    assert result is None
    assert calls == []


def test_success_rereads_original_plan_and_conflict_never_replays():
    calls = []
    def replay(payload):
        calls.append(payload)
        return "replay", {"plan_id": "original"}, None
    def run(result):
        return issue_returned_head(PAYLOAD, already_issued=lambda p: False,
                                   replay=replay, dispatch=lambda p: (None, result))
    outcome, result = run({"issued": True, "position_study_mode": "position", "position_presentation": {"stale": True}})
    assert outcome is None
    assert result["position_presentation"] == {"plan_id": "original"}
    assert calls == [PAYLOAD]
    calls.clear()
    outcome, result = run({"issued": False, "position_study_mode": "position", "position_study_hold": "position_attempt_payload_conflict"})
    assert outcome == {"kind": "held", "reason": "position_attempt_payload_conflict"}
    assert result is None
    assert calls == []


def test_success_without_original_plan_is_held():
    outcome, result = issue_returned_head(
        PAYLOAD, already_issued=lambda p: False,
        replay=lambda p: ("legacy", None, None),
        dispatch=lambda p: (None, {"issued": True, "position_study_mode": "position"}),
    )
    assert outcome == {"kind": "held", "reason": "issued_plan_unavailable"}
    assert result is None


def test_replay_without_a_plan_never_reports_issued():
    outcome, result = issue_returned_head(PAYLOAD, already_issued=lambda p: True,
        replay=lambda p: ("replay", None, None), dispatch=lambda p: (None, {"issued": True}))
    assert outcome is None
    assert result == {"issued": False, "position_study_mode": "position",
                      "position_presentation": None, "position_study_hold": "position_plan_missing"}


def test_prepared_head_rejects_unknown_mode():
    import pytest
    from app.services.queue_next_card import PreparedQueueHead
    with pytest.raises(ValueError, match="Unknown prepared queue head mode"):
        PreparedQueueHead(PAYLOAD, "invented")


def test_unknown_issued_mode_is_not_accepted_as_legacy():
    outcome, result = issue_returned_head(PAYLOAD, already_issued=lambda p: False,
        replay=lambda p: ("legacy", None, None),
        dispatch=lambda p: (None, {"issued": True, "position_study_mode": "unknown"}))
    assert outcome == {"kind": "unavailable", "reason": "queue_changed", "retryable": True}
    assert result is None


def test_prepared_head_rejects_impossible_mode_proposal_pairs():
    import pytest
    from app.services.queue_next_card import PreparedQueueHead
    for mode, proposal, reason in [("position", None, None), ("replay", None, None),
                                   ("legacy", object(), None), ("hold", None, None),
                                   ("legacy", None, "wrong")]:
        with pytest.raises(ValueError):
            PreparedQueueHead(PAYLOAD, mode, proposal, reason)
