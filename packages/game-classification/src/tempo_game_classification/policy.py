"""Pure classification policy; callers supply facts, never live capabilities."""
from datetime import datetime
from typing import Literal, Sequence


FindingKind = Literal[
    "confirmed_miss", "opponent_gap", "missing_own_continuation",
    "known_unintroduced", "known_unavailable", "conflict", "ambiguous", "coverage_end",
]


def classify_response(
    *, played_uci: str, expected_uci: Sequence[str], learner_turn: bool,
    complete: bool = True, terminal: bool = False, ambiguous: bool = False,
    mapping_count: int = 0, available: bool = False,
    introduced: bool | None = None, introduced_at: datetime | None = None,
    introduced_before_game: bool | None = None,
    occurred_at: datetime | None = None, occurrence_is_exact: bool = False,
    introduction_matches_content: bool = False,
) -> tuple[FindingKind, str] | None:
    """Classify one authoritative occurrence, including unintroduced encounters.

    A correct unintroduced encounter is a frequency fact, never a failure.
    None means there is no reportable finding for the supplied facts. Historical
    non-lapse findings do not decide current Study admission or priority.

    Route selection, exact identity, and decision/card mapping belong to the
    supplied index. Any introduced_before_game assertion must already be scoped
    to this exact game source by the adapter. This function cannot infer it from
    a FEN or current card.
    An incomplete snapshot cannot prove absence. Own-response conflicts remain
    conflicts even when the played move is one of the competing responses.
    """
    expected = frozenset(expected_uci)
    if ambiguous:
        return "ambiguous", "repertoire_or_decision_identity_ambiguous"
    if learner_turn and len(expected) > 1:
        return "conflict", "multiple_own_responses_in_one_repertoire"
    if not complete:
        return "known_unavailable", "snapshot_incomplete_or_unavailable"
    if not expected:
        if terminal:
            return "coverage_end", "deliberate_authored_coverage_end"
        return ("missing_own_continuation", "own_continuation_not_authored") if learner_turn else (
            "opponent_gap", "opponent_reply_not_authored")
    matched = played_uci in expected
    if matched and (not learner_turn or terminal or mapping_count != 1 or not available):
        return None
    if terminal:
        return "coverage_end", "terminal_route_cannot_prove_missing_continuation"
    if not learner_turn:
        return "opponent_gap", "opponent_reply_not_authored"
    if mapping_count > 1:
        return "ambiguous", "current_presentation_mapping_ambiguous"
    if mapping_count != 1 or not available:
        return "known_unavailable", "current_presentation_unavailable"
    if introduced is False:
        return "known_unintroduced", "decision_not_introduced"
    if introduced_before_game is False:
        return "known_unintroduced", "introduction_not_before_game"
    if introduced is not True:
        return None if matched else ("known_unavailable", "introduction_evidence_unavailable")
    if not occurrence_is_exact or occurred_at is None:
        return None if matched else ("known_unavailable", "game_occurrence_time_not_exact")
    if occurred_at.tzinfo is None or (introduced_at is not None and introduced_at.tzinfo is None):
        return None if matched else ("known_unavailable", "chronology_requires_timezone")
    if introduced_at is not None and introduced_at >= occurred_at:
        if introduced_before_game is True:
            return None if matched else ("known_unavailable", "conflicting_introduction_chronology")
        return "known_unintroduced", "introduction_not_before_game"
    if introduced_at is None and introduced_before_game is not True:
        return None if matched else ("known_unavailable", "introduction_evidence_unavailable")
    if matched:
        return None
    if not introduction_matches_content:
        return "known_unavailable", "introduced_content_identity_unproved"
    return "confirmed_miss", "unique_introduced_response_not_played"
