"""Pure exact-encounter guards, independent of weighted allocation and storage."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from app.contracts.product_v02.repertoire import ImmutableAuthoredRoute
from .results import CommitResult

if TYPE_CHECKING:
    from .ports import IntroductionConflicts


def validate_study_day(day):
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError("explicit local Study day required")


def introduction_is_held(conflicts: IntroductionConflicts | None, *, repertoire_id: str,
                         presentation_id: str, presentation_revision: int) -> bool:
    """Delegate current suspension to its Study owner, without copying policy.

    No capability preserves the prior behavior. An injected capability must
    explicitly prove False to permit admission; unknown is never a release.
    """
    if conflicts is None:
        return False
    if type(presentation_revision) is not int or presentation_revision < 1:
        return True
    return conflicts.blocked(repertoire_id, presentation_id, presentation_revision) is not False


@dataclass(frozen=True)
class TargetAlternatives:
    routes: tuple[ImmutableAuthoredRoute, ...]
    reason: str | None = None


def target_alternatives(routes, *, expected: tuple[tuple[str, str], ...], learner_color: str) -> TargetAlternatives:
    """Complete relevant prescriptions, retaining no unrelated positions/index.

    Reuse the current repertoire reader's finite 10k-route/100k-ply discovery
    limits. The existing 64-*relevant*-route guard is unchanged. No response
    filter is allowed before checking alternatives, including transpositions.
    """
    import chess
    from tempo_evidence_math import opening_position_key
    wanted = dict(expected)
    if not wanted or len(wanted) != len(expected):
        return TargetAlternatives((), 'target_context_unproved')
    responses = {position:set() for position in wanted}
    relevant, seen, plies = [], set(), 0
    try:
        for route in routes:
            if route.route_id in seen or len(seen) >= 10000:
                return TargetAlternatives((), 'target_discovery_bound')
            seen.add(route.route_id)
            plies += len(route.moves)
            if plies > 100000 or len(route.moves) > 1000:
                return TargetAlternatives((), 'target_discovery_bound')
            if route.learner_color != learner_color:
                return TargetAlternatives((), 'target_source_color_unproved')
            board = chess.Board(route.starting_fen)
            if not board.is_valid():
                return TargetAlternatives((), 'target_source_legality_unproved')
            touches = False
            for uci in route.moves:
                position = opening_position_key(board.fen())
                move = chess.Move.from_uci(uci)
                if move not in board.legal_moves:
                    return TargetAlternatives((), 'target_source_legality_unproved')
                if board.turn == (learner_color == 'white') and position in wanted:
                    touches = True
                    responses[position].add(uci)
                    if uci != wanted[position]:
                        return TargetAlternatives((), 'target_response_conflict')
                board.push(move)
            touches |= board.turn == (learner_color == 'white') and opening_position_key(board.fen()) in wanted
            if touches:
                relevant.append(route)
                if len(relevant) > 64:
                    return TargetAlternatives((), 'target_alternatives_bound')
    except (ValueError, TypeError, KeyError):
        return TargetAlternatives((), 'target_source_legality_unproved')
    if any(responses[position] != {move} for position,move in wanted.items()):
        return TargetAlternatives((), 'target_context_unproved')
    return TargetAlternatives(tuple(relevant))


def ordinary_encounter_allowance(*, study_only, global_limit, repertoire_limit,
                                introduction_count, repertoire_count):
    """Saved ordinary caps over canonical introductions, without D allocation.

    The queue owner counts each actual physical introduction today once.
    A shared presentation consumes one global slot and its admitting membership's
    ordinary allowance. No reserve, weight default or separate ledger is created.
    """
    if study_only:
        return 'study_only'
    if (type(global_limit) is not int or global_limit < 0
            or (repertoire_limit is not None and
                (type(repertoire_limit) is not int or repertoire_limit < 0))):
        return 'current_controls_unproved'
    if global_limit == 0 or repertoire_limit == 0:
        return 'zero_limit'
    limit = global_limit if repertoire_limit is None else repertoire_limit
    if (type(introduction_count) is not int or introduction_count < 0
            or type(repertoire_count) is not int or not 0 <= repertoire_count <= introduction_count):
        return 'current_controls_unproved'
    if introduction_count >= global_limit or repertoire_count >= limit:
        return 'introduction_allowance'
    return None


def encounter_allowance(*, allowance, paused, study_only, global_limit, repertoire_limit,
                        introduction_count, repertoire_count):
    """Explicit real-game exception; a full pause is a separate control.

    Zero ordinary allowances and Study-only remain unchanged for ordinary work.
    No exception is inferred from a source, budget, or missing configuration.
    """
    if allowance not in {'ordinary', 'encounter_exception'} or type(paused) is not bool:
        raise ValueError('explicit encounter allowance and full-pause control required')
    if paused:
        return 'paused'
    if allowance == 'encounter_exception':
        return None
    return ordinary_encounter_allowance(study_only=study_only,global_limit=global_limit,
        repertoire_limit=repertoire_limit,introduction_count=introduction_count,repertoire_count=repertoire_count)


