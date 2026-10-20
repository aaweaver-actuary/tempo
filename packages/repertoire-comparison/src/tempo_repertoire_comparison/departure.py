"""Minimal authored-membership wrapper: one-based earliest departure only."""
from typing import Literal, TypedDict
from tempo_game_review_contracts import CanonicalGame, RepertoireSnapshot
from .comparison import compare


class DepartureResult(TypedDict):
    status: Literal["compared", "unsupported"]
    ply: int | None
    side: Literal["learner", "opponent"] | None
    reason: str | None


def first_departure(game: CanonicalGame, snapshot: RepertoireSnapshot) -> DepartureResult:
    """Any supplied authored alternative counts as coverage; no recall inference."""
    try:
        report = compare(game, snapshot)
    except ValueError as error:
        return dict(status="unsupported", ply=None, side=None, reason=str(error))
    for fact in report.positions:
        # Different authored policies may offer alternatives. Membership needs
        # only one matching route; it grants no policy/action authority.
        if fact.reason == "ambiguous":
            if fact.actual_move in fact.expected_moves:
                continue
        elif fact.status == "unknown":
            return dict(status="unsupported", ply=None, side=None, reason=fact.reason)
        elif fact.status == "matched":
            continue
        return dict(status="compared", ply=fact.ply + 1, side=fact.side, reason=None)
    return dict(status="compared", ply=None, side=None, reason=None)


def first_departure_ply(game: CanonicalGame, snapshot: RepertoireSnapshot) -> int | None:
    """Return the one-based integer/null; insufficient input is an explicit error."""
    result = first_departure(game, snapshot)
    if result["status"] == "unsupported":
        raise ValueError(result["reason"])
    return result["ply"]
