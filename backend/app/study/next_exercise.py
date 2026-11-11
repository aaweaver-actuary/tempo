"""Study's issuance decision, independent of transport and storage."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..contracts.queue_issuance import NextExerciseOutcome, immutable_issuance_conflict


@dataclass
class QueueReadSnapshot:
    """Detached pre-issuance facts; cards remain mutable for serving."""

    day: str
    evidence_timezone: str | None
    projection: dict[str, Any] | None
    diagnostics: list[dict[str, Any]]
    cards: list[dict[str, Any]]
    total_count: int


# A None outcome carries an intermediate issuance result, not a ready queue.
# The serving owner must still revalidate the day and head before presenting it.
def issue_returned_head(
    payload: dict[str, Any],
    *,
    already_issued: Callable[[dict[str, Any]], bool],
    replay: Callable[[dict[str, Any]], tuple[str, dict[str, Any] | None, str | None]],
    dispatch: Callable[[dict[str, Any]], tuple[NextExerciseOutcome | None, dict[str, Any] | None]],
) -> tuple[NextExerciseOutcome | None, dict[str, Any] | None]:
    def original_plan(plan: dict[str, Any]) -> dict[str, Any]:
        return {"issued": True, "position_study_mode": "position",
                "position_presentation": plan, "position_study_hold": None}

    if already_issued(payload):
        mode, plan, reason = replay(payload)
        if mode == "replay":
            return None, original_plan(plan)
        if mode != "legacy":
            return None, {"issued": False, "position_study_mode": "position",
                          "position_presentation": None,
                          "position_study_hold": reason or "position_plan_missing"}
        return None, {"issued": True}

    outcome, result = dispatch(payload)
    if outcome is not None:
        return outcome, None
    assert result is not None
    if result.get("position_study_mode") == "position":
        conflict = immutable_issuance_conflict(result)
        if conflict:
            return {"kind": "held", "reason": conflict}, None
        if result.get("issued"):
            mode, plan, _ = replay(payload)
            if mode != "replay":
                return {"kind": "held", "reason": "issued_plan_unavailable"}, None
            return None, original_plan(plan)
        return None, result
    if not result.get("issued"):
        return {"kind": "unavailable", "reason": "queue_changed", "retryable": True}, None
    return None, result


def next_exercise(*, read_snapshot, issue, current_head, finalize,
                  manifests, replay, is_hold, issued_manifest, shape_queue,
                  include_opening_evidence: bool, prepare=None) -> NextExerciseOutcome:
    """Complete the existing queue selection, issuance and presentation sequence."""
    snapshot = read_snapshot()
    cards = snapshot.cards
    if cards:
        outcome, issuance = issue(snapshot.day, cards[0])
        if outcome is not None:
            return outcome
        assert issuance is not None
        if issuance.get("issued"):
            day, rows = current_head()
            identity = lambda card: (card["queue_entry_id"], card["id"], card["revision"])
            if day != snapshot.day or not rows or identity(rows[0]) != identity(cards[0]):
                return {"kind": "unavailable", "reason": "head_changed", "retryable": True}
        if issuance.get("position_study_mode") == "position":
            cards[0].update({key: issuance[key] for key in (
                "position_study_mode", "position_presentation", "position_study_hold")})

    finalize(cards)
    position_head = bool(cards and cards[0].get("position_presentation"))
    if include_opening_evidence or position_head:
        manifests(cards if include_opening_evidence else cards[:1])
        if position_head:
            card = cards[0]
            plan = replay(card["position_presentation"]["attempt_id"])
            try:
                if is_hold(plan) or plan is None or "opening_decision_manifest" not in card:
                    raise ValueError("position_manifest_scope_unavailable")
                card["opening_decision_manifest"] = issued_manifest(plan, card["opening_decision_manifest"])
            except (TypeError, ValueError, KeyError):
                card["position_presentation"] = None
                card["position_study_hold"] = "position_manifest_scope_unavailable"
        for card in cards:
            if "opening_decision_manifest" in card:
                card["opening_evidence_study_timezone"] = snapshot.evidence_timezone or "local"
    queue = shape_queue(snapshot.day, cards, snapshot.total_count, snapshot.diagnostics, snapshot.projection)
    if prepare is not None:
        prepare(queue)
    projection_state = queue["projection"]["state"]
    if projection_state == "failed":
        return {"kind": "unavailable", "reason": "queue_projection_failed", "retryable": True, "queue": queue}
    if cards and cards[0].get("position_study_hold") and not cards[0].get("position_presentation"):
        return {"kind": "held", "reason": cards[0]["position_study_hold"], "queue": queue}
    if not cards and projection_state == "refreshing":
        return {"kind": "held", "reason": "queue_projection_refreshing", "queue": queue}
    return {"kind": "ready" if cards else "empty", "queue": queue}
