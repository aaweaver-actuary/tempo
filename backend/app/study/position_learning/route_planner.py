"""Choose one certified authored route and, when proved, skip its clean prefix.

This is a bounded pure planner. Repertoire owns route legality and source
integrity; Study owns the saved receipts represented by prefix proofs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import chess

from .contracts import DecisionOccurrence, Hold, PositionKey, RouteOption, SourceCertificate
from .prefix_eligibility import CleanPresentationProof, verify_clean_prefix


@dataclass(frozen=True)
class Plan:
    """One frozen route suffix retaining its original authored occurrences."""

    position: PositionKey
    route_id: str
    target_ply: int
    starting_fen: str
    start_ply: int
    moves_uci: tuple[str, ...]
    opponent_cue_uci: str | None
    opponent_cue_ply: int | None
    skipped_prefix_decisions: tuple[DecisionOccurrence, ...]
    decision_occurrences: tuple[DecisionOccurrence, ...]
    prefix_credit_attempt_id: str | None
    source_certificate: SourceCertificate


def _fen_at(route: RouteOption, ply: int) -> str | None:
    """Materialize the already-validated route position at a bounded ply."""
    try:
        board = chess.Board(route.start_fen)
        for uci in route.moves_uci[:ply]:
            move = chess.Move.from_uci(uci)
            if move not in board.legal_moves:
                return None
            board.push(move)
        return board.fen()
    except (ValueError, TypeError):
        return None


def _candidate_order(item: tuple[RouteOption, DecisionOccurrence]) -> tuple:
    route, decision = item
    return (
        route.route_id,
        decision.ply,
        route.moves_uci,
        route.certificate.snapshot_id,
        route.certificate.source_revision,
        route.certificate.source_digest,
    )


def choose_plan(
    target: PositionKey,
    routes: tuple[RouteOption, ...],
    *,
    clean_proofs: tuple[CleanPresentationProof, ...] = (),
    study_day: date,
    owner_epoch: str,
) -> Plan | Hold:
    """Choose one deterministic legal route for ``target``.

    A clean proof can suppress only the exact ordered route prefix returned by
    ``verify_clean_prefix``. If none applies, the complete authored route is
    shown. The candidate order is stable and does not search for an optimal
    transposition.
    """
    if not owner_epoch:
        return Hold("daily_prefix_scope_unproven")

    candidates = tuple(
        (route, decision)
        for route in routes
        for decision in route.decisions
        if decision.definition.key == target
    )
    if not candidates:
        return Hold("no_exact_target_route")

    definitions = {decision.definition for _, decision in candidates}
    if len(definitions) != 1:
        return Hold("conflicting_target_definitions")
    source_fences = {
        (route.certificate.snapshot_id, route.certificate.source_revision,
         route.certificate.source_digest)
        for route, _ in candidates
    }
    if len(source_fences) != 1:
        return Hold("mixed_route_source_certificates")

    candidates = tuple(sorted(candidates, key=_candidate_order))

    # If a whole-clean saved attempt already includes the target response on
    # this exact source and owner day, there is no new test to issue.
    for route, target_decision in candidates:
        for proof in clean_proofs:
            eligibility = verify_clean_prefix(
                route,
                proof,
                target_ply=target_decision.ply,
                study_day=study_day,
                owner_epoch=owner_epoch,
            )
            if eligibility is not None and eligibility.target_is_clean:
                return Hold("position_already_cleanly_tested_today")

    for route, target_decision in candidates:
        if not (
            route.legal_verified
            and route.certificate.complete
            and route.certificate.current_integrity
        ):
            continue

        applicable = []
        for proof in clean_proofs:
            eligibility = verify_clean_prefix(
                route,
                proof,
                target_ply=target_decision.ply,
                study_day=study_day,
                owner_epoch=owner_epoch,
            )
            if eligibility is not None and eligibility.first_uncovered_decision is not None:
                applicable.append(eligibility)

        # Prefer the proof that establishes the longest common move prefix;
        # ties are resolved by stable attempt ID.
        selected_credit = min(
            applicable,
            key=lambda value: (-value.shared_move_count, value.attempt_id),
            default=None,
        )

        start_ply = 0
        skipped: tuple[DecisionOccurrence, ...] = ()
        credit_attempt_id = None
        if selected_credit is not None:
            first = selected_credit.first_uncovered_decision
            skipped = selected_credit.skipped_decisions
            credit_attempt_id = selected_credit.attempt_id
            # Include the immediately preceding opponent move as the cue for
            # the first uncovered learner response. The exact learner decision
            # FEN and original ply remain in decision_occurrences below.
            start_ply = first.ply - 1 if first.ply > 0 else 0

        start_fen = _fen_at(route, start_ply)
        if start_fen is None:
            continue
        cue_ply = (
            start_ply
            if selected_credit is not None
            and start_ply < selected_credit.first_uncovered_decision.ply
            else None
        )
        cue = route.moves_uci[cue_ply] if cue_ply is not None else None
        decisions = tuple(
            decision
            for decision in route.decisions
            if selected_credit is None
            or decision.ply >= selected_credit.first_uncovered_decision.ply
        )
        if not decisions:
            continue

        return Plan(
            position=target,
            route_id=route.route_id,
            target_ply=target_decision.ply,
            starting_fen=start_fen,
            start_ply=start_ply,
            moves_uci=route.moves_uci[start_ply:],
            opponent_cue_uci=cue,
            opponent_cue_ply=cue_ply,
            skipped_prefix_decisions=skipped,
            decision_occurrences=decisions,
            prefix_credit_attempt_id=credit_attempt_id,
            source_certificate=route.certificate,
        )

    return Hold("no_certified_target_route")
