"""Study's issuance decision, independent of transport and storage.

Cards and envelopes keep opaque transport extensions at this boundary. Required
head facts are validated into an immutable identity before any owner is called.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, NotRequired, Protocol, TypedDict, runtime_checkable

from ..contracts.queue_issuance import NextExerciseOutcome, immutable_issuance_conflict


@dataclass(frozen=True)
class HeadIdentity:
    queue_entry_id: int
    card_id: str
    revision: int


def head_identity(card: dict[str, object]) -> HeadIdentity | None:
    entry, card_id, revision = card.get("queue_entry_id"), card.get("id"), card.get("revision")
    if (type(entry) is not int or entry <= 0 or not isinstance(card_id, str) or not card_id
            or type(revision) is not int or revision <= 0):
        return None
    return HeadIdentity(entry, card_id, revision)


class IssueIdentity(TypedDict):
    queue_date: str
    entry_id: int
    card_id: str
    expected_revision: int


class Issuance(TypedDict):
    issued: bool
    position_study_mode: NotRequired[Literal["position"]]
    position_presentation: NotRequired[dict[str, object] | None]
    position_study_hold: NotRequired[str | None]


ReplayResult = (tuple[Literal["legacy"], None, None] | tuple[Literal["position"], None, None]
                | tuple[Literal["hold"], None, str | None] | tuple[Literal["replay"], dict[str, object], None])


@runtime_checkable
class IssuedPlanFact(Protocol):
    """The retained server plan passed intact to its manifest owner."""
    attempt_id: str


class HoldFact(Protocol):
    reason: str
IssueResult = tuple[NextExerciseOutcome, None] | tuple[None, Issuance]


def _issued(result: Issuance) -> IssueResult:
    return None, result


def _stopped(outcome: NextExerciseOutcome) -> IssueResult:
    return outcome, None


@dataclass
class QueueReadSnapshot:
    """Detached pre-issuance facts; callbacks intentionally finalize cards in place."""

    day: str
    evidence_timezone: str | None
    projection: dict[str, object] | None
    diagnostics: list[dict[str, object]]
    cards: list[dict[str, object]]
    total_count: int


def issue_returned_head(
    payload: IssueIdentity,
    *,
    already_issued: Callable[[IssueIdentity], bool],
    replay: Callable[[IssueIdentity], ReplayResult],
    dispatch: Callable[[IssueIdentity], IssueResult],
) -> IssueResult:
    def original_plan(plan: dict[str, object]) -> Issuance:
        return {"issued": True, "position_study_mode": "position",
                "position_presentation": plan, "position_study_hold": None}

    if already_issued(payload):
        mode, plan, reason = replay(payload)
        if mode == "replay" and plan is not None:
            return _issued(original_plan(plan))
        if mode != "legacy":
            return _issued({"issued": False, "position_study_mode": "position",
                            "position_presentation": None,
                            "position_study_hold": reason or "position_plan_missing"})
        return _issued({"issued": True})

    outcome, result = dispatch(payload)
    if outcome is not None:
        return _stopped(outcome)
    if result is None or type(result.get("issued")) is not bool:
        return _stopped({"kind": "unavailable", "reason": "queue_changed", "retryable": True})
    if "position_study_mode" in result and result.get("position_study_mode") != "position":
        return _stopped({"kind": "unavailable", "reason": "queue_changed", "retryable": True})
    if result.get("position_study_mode") == "position":
        conflict = immutable_issuance_conflict(result)
        if conflict:
            return _stopped({"kind": "held", "reason": conflict})
        if result.get("issued"):
            mode, plan, _ = replay(payload)
            if mode != "replay" or plan is None:
                return _stopped({"kind": "held", "reason": "issued_plan_unavailable"})
            return _issued(original_plan(plan))
        return _issued(result)
    if not result.get("issued"):
        return _stopped({"kind": "unavailable", "reason": "queue_changed", "retryable": True})
    return _issued(result)


def next_exercise(*, read_snapshot: Callable[[], QueueReadSnapshot],
                  issue: Callable[[str, dict[str, object]], IssueResult],
                  current_head: Callable[[], tuple[str, list[dict[str, object]]]],
                  finalize: Callable[[list[dict[str, object]]], None],
                  manifests: Callable[[list[dict[str, object]]], None],
                  replay: Callable[[str], IssuedPlanFact | HoldFact | None], is_hold: Callable[[object], bool],
                  issued_manifest: Callable[[IssuedPlanFact, object], object],
                  shape_queue: Callable[[str, list[dict[str, object]], int, list[dict[str, object]], dict[str, object] | None], dict[str, object]],
                  include_opening_evidence: bool,
                  prepare: Callable[[dict[str, object]], None] | None = None) -> NextExerciseOutcome:
    """Complete queue selection and issuance; callbacks mutate their supplied cards."""
    snapshot = read_snapshot()
    if not isinstance(snapshot.day, str) or not snapshot.day or type(snapshot.total_count) is not int or snapshot.total_count < 0:
        return {"kind": "unavailable", "reason": "queue_changed", "retryable": True}
    cards = snapshot.cards
    if cards:
        original = head_identity(cards[0])
        if original is None:
            return {"kind": "unavailable", "reason": "head_changed", "retryable": True}
        outcome, issuance = issue(snapshot.day, cards[0])
        if outcome is not None:
            return outcome
        if issuance is None or type(issuance.get("issued")) is not bool:
            return {"kind": "unavailable", "reason": "queue_changed", "retryable": True}
        if "position_study_mode" in issuance and issuance.get("position_study_mode") != "position":
            return {"kind": "unavailable", "reason": "queue_changed", "retryable": True}
        if not issuance["issued"]:
            issue_hold = issuance.get("position_study_hold")
            if (issuance.get("position_study_mode") != "position" or not isinstance(issue_hold, str)
                    or not issue_hold or issuance.get("position_presentation") is not None):
                return {"kind": "unavailable", "reason": "queue_changed", "retryable": True}
        elif issuance.get("position_study_mode") == "position":
            issued_presentation = issuance.get("position_presentation")
            if (not isinstance(issued_presentation, dict) or not isinstance(issued_presentation.get("attempt_id"), str)
                    or not issued_presentation["attempt_id"] or issuance.get("position_study_hold") is not None):
                return {"kind": "held", "reason": "issued_plan_unavailable"}
        if issuance.get("issued"):
            day, rows = current_head()
            if day != snapshot.day or not rows or head_identity(rows[0]) != original:
                return {"kind": "unavailable", "reason": "head_changed", "retryable": True}
        if issuance.get("position_study_mode") == "position":
            cards[0].update({key: issuance.get(key) for key in (
                "position_study_mode", "position_presentation", "position_study_hold")})

    finalize(cards)
    position_head = bool(cards and cards[0].get("position_presentation"))
    if include_opening_evidence or position_head:
        manifests(cards if include_opening_evidence else cards[:1])
        if position_head:
            card = cards[0]
            presentation = card.get("position_presentation")
            if isinstance(presentation, dict) and isinstance(presentation.get("attempt_id"), str):
                plan = replay(presentation["attempt_id"])
            else:
                plan = None
            try:
                if (is_hold(plan) or not isinstance(plan, IssuedPlanFact) or not isinstance(plan.attempt_id, str)
                        or not plan.attempt_id or "opening_decision_manifest" not in card):
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
    projection = queue.get("projection")
    if not isinstance(projection, dict) or not isinstance(projection.get("state"), str):
        return {"kind": "unavailable", "reason": "queue_projection_failed", "retryable": True, "queue": queue}
    projection_state = projection["state"]
    if projection_state not in ("ready", "refreshing", "failed") or projection_state == "failed":
        return {"kind": "unavailable", "reason": "queue_projection_failed", "retryable": True, "queue": queue}
    if cards and cards[0].get("position_study_hold") and not cards[0].get("position_presentation"):
        reason = cards[0]["position_study_hold"]
        return {"kind": "held", "reason": reason if isinstance(reason, str) else "position_plan_missing", "queue": queue}
    if not cards and projection_state == "refreshing":
        return {"kind": "held", "reason": "queue_projection_refreshing", "queue": queue}
    if cards:
        return {"kind": "ready", "queue": queue}
    return {"kind": "empty", "queue": queue}
