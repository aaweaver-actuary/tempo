import copy
from types import SimpleNamespace

import pytest

from app.study.next_exercise import QueueReadSnapshot, next_exercise


CARD = {"queue_entry_id": 1, "id": "card", "revision": 3}


def run(cards=None, **overrides):
    cards = copy.deepcopy([CARD] if cards is None else cards)
    calls = []
    snapshot = QueueReadSnapshot("day", "Europe/Rome", {"state": "refreshing"}, [], cards, len(cards))
    def issue(day, card):
        calls.append("issue")
        return None, {"issued": True}
    def current():
        calls.append("current")
        return "day", [CARD]
    def manifest(items):
        calls.append(("manifest", len(items)))
        for item in items:
            item["opening_decision_manifest"] = {"scope": "original"}
    def replay(attempt):
        calls.append(("replay", attempt))
        return SimpleNamespace(attempt_id="original")
    args = dict(read_snapshot=lambda: snapshot, issue=issue, current_head=current,
        finalize=lambda cards: calls.append("finalize"),
        manifests=manifest, replay=replay, is_hold=lambda plan: plan == "hold",
        issued_manifest=lambda plan, scope: {"plan": plan, "scope": scope},
        shape_queue=lambda day, cards, count, diagnostics, projection: {"cards": cards, "projection": projection},
        include_opening_evidence=False)
    args.update(overrides)
    return next_exercise(**args), calls


def test_ready_rechecks_before_finalizing():
    result, calls = run()
    assert result["kind"] == "ready"
    assert calls == ["issue", "current", "finalize"]


@pytest.mark.parametrize("current", [("other", [CARD]), ("day", []), *[("day", [{**CARD, key: 99}]) for key in ("queue_entry_id", "id", "revision")]])
def test_changed_day_or_head_stops_later_effects(current):
    result, calls = run(current_head=lambda: current)
    assert result == {"kind": "unavailable", "reason": "head_changed", "retryable": True}
    assert calls == ["issue"]


@pytest.mark.parametrize("outcome", [{"kind": "pending", "operation_id": "op"}, {"kind": "held", "reason": "position_attempt_identity_conflict"}, {"kind": "unavailable", "reason": "queue_changed", "retryable": True}])
def test_terminal_issuance_stops_all_later_effects(outcome):
    result, calls = run(issue=lambda day, card: (outcome, None))
    assert result == outcome
    assert calls == []


def position_issue(day, card):
    return None, {"issued": True, "position_study_mode": "position", "position_presentation": {"attempt_id": "original"}, "position_study_hold": None}


def test_position_replays_original_attempt_and_head_manifest():
    result, calls = run([CARD, {**CARD, "id": 5}], issue=position_issue)
    head = result["queue"]["cards"][0]
    assert calls == ["current", "finalize", ("manifest", 1), ("replay", "original")]
    assert head["opening_decision_manifest"]["plan"].attempt_id == "original"
    assert head["opening_evidence_study_timezone"] == "Europe/Rome"


@pytest.mark.parametrize("replay", [lambda attempt: None, lambda attempt: "hold"])
def test_missing_or_held_plan_clears_presentation(replay):
    result, _ = run(issue=position_issue, replay=replay)
    card = result["queue"]["cards"][0]
    assert card["position_presentation"] is None
    assert card["position_study_hold"] == "position_manifest_scope_unavailable"


def test_missing_manifest_holds_and_negotiated_scope_covers_all_cards():
    result, calls = run([CARD, {**CARD, "id": 5}], issue=position_issue,
                        include_opening_evidence=True, manifests=lambda cards: calls_placeholder(cards))
    assert result["queue"]["cards"][0]["position_study_hold"] == "position_manifest_scope_unavailable"


def calls_placeholder(cards):
    assert len(cards) == 2


def test_empty_refreshing_and_priority_remain_distinct():
    result, calls = run([])
    assert result["kind"] == "held" and result["reason"] == "queue_projection_refreshing"
    assert result["queue"]["projection"] == {"state": "refreshing"}
    assert calls == ["finalize"]


def test_source_failures_are_not_swallowed():
    def failed(attempt):
        raise RuntimeError("source unavailable")
    with pytest.raises(RuntimeError, match="source unavailable"):
        run(issue=position_issue, replay=failed)


def test_invalid_issued_manifest_holds_with_flat_queue():
    def invalid(plan, scope):
        raise ValueError("invalid manifest")
    result, _ = run(issue=position_issue, issued_manifest=invalid)
    assert result["kind"] == "held" and result["reason"] == "position_manifest_scope_unavailable"
    assert result["queue"]["cards"][0]["position_study_hold"] == "position_manifest_scope_unavailable"


def test_unissued_position_hold_does_not_recheck_current_head():
    def held(day, card):
        return None, {"issued": False, "position_study_mode": "position", "position_presentation": None, "position_study_hold": "position_not_issued"}
    result, calls = run(issue=held)
    assert calls == ["finalize"]
    assert result["kind"] == "held" and result["reason"] == "position_not_issued"
    assert result["queue"]["cards"][0]["position_study_hold"] == "position_not_issued"


@pytest.mark.parametrize("state,cards,expected_kind,reason", [
    ("refreshing", [], "held", "queue_projection_refreshing"),
    ("failed", [], "unavailable", "queue_projection_failed"),
    ("failed", [CARD], "unavailable", "queue_projection_failed"),
    ("ready", [], "empty", None),
    ("refreshing", [CARD], "ready", None),
])
def test_projection_outcome_preserves_exact_flat_wire(state, cards, expected_kind, reason):
    from app.product_composition.study import _render_outcome
    from app.study.queue_serving import queue_payload

    projection = {"state": state, "generation": 7, "updated_at": None,
                  "refresh_pending": int(state == "refreshing"), "last_error": "failure" if state == "failed" else None, "blocked_count": 0}
    snapshot = QueueReadSnapshot("day", None, projection, [], copy.deepcopy(cards), len(cards))
    result, _ = run(cards, read_snapshot=lambda: snapshot, shape_queue=queue_payload)
    expected = {"local_date": "day", "cards": cards, "count": len(cards), "diagnostics": [], "projection": projection}
    assert result["kind"] == expected_kind
    assert result.get("reason") == reason
    assert _render_outcome(result) == expected


@pytest.mark.parametrize("manifest_missing", [False, True])
def test_position_hold_preserves_exact_flat_wire(manifest_missing):
    from app.product_composition.study import _render_outcome
    from app.study.queue_serving import queue_payload

    projection = {"state": "ready"}
    snapshot = QueueReadSnapshot("day", "Europe/Rome", projection, [], [copy.deepcopy(CARD)], 1)
    def held(day, card):
        return None, {"issued": False, "position_study_mode": "position", "position_presentation": None, "position_study_hold": "position_not_issued"}
    issue = position_issue if manifest_missing else held
    result, _ = run(read_snapshot=lambda: snapshot, issue=issue, shape_queue=queue_payload, manifests=lambda cards: None)
    reason = "position_manifest_scope_unavailable" if manifest_missing else "position_not_issued"
    expected_card = {**CARD, "position_study_mode": "position", "position_presentation": None, "position_study_hold": reason}
    expected = {"local_date": "day", "cards": [expected_card], "count": 1, "diagnostics": [], "projection": projection}
    assert result == {"kind": "held", "reason": reason, "queue": expected}
    assert _render_outcome(result) == expected


@pytest.mark.parametrize("field,value", [("queue_entry_id", None), ("id", None), ("revision", None), ("revision", 0), ("queue_entry_id", True)])
def test_invalid_original_identity_never_issues(field, value):
    card = {**CARD, field: value}
    result, calls = run([card])
    assert result == {"kind": "unavailable", "reason": "head_changed", "retryable": True}
    assert calls == []


def test_unknown_projection_never_reports_ready():
    snapshot = QueueReadSnapshot("day", None, {"state": "unknown"}, [], [CARD.copy()], 1)
    result, _ = run(read_snapshot=lambda: snapshot)
    assert result["kind"] == "unavailable"
    assert result["reason"] == "queue_projection_failed"


def test_replay_source_type_error_is_not_silenced():
    def failed(_attempt):
        raise TypeError("source malfunction")
    with pytest.raises(TypeError, match="source malfunction"):
        run(issue=position_issue, replay=failed)


@pytest.mark.parametrize("issuance", [None, {}, {"issued": False}, {"issued": True, "position_study_mode": "invalid"},
    {"issued": False, "position_study_mode": "position", "position_study_hold": None}])
def test_malformed_issuance_never_reports_ready(issuance):
    result, calls = run(issue=lambda day, card: (None, issuance))
    assert result == {"kind": "unavailable", "reason": "queue_changed", "retryable": True}
    assert calls == []


@pytest.mark.parametrize("presentation", [None, {}, {"attempt_id": ""}])
def test_issued_position_without_original_attempt_is_held(presentation):
    result, calls = run(issue=lambda day, card: (None, {"issued": True, "position_study_mode": "position", "position_presentation": presentation}))
    assert result == {"kind": "held", "reason": "issued_plan_unavailable"}
    assert calls == []
