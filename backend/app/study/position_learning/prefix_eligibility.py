"""Pure proof checks for skipping an already completed authored prefix.

The caller must build ``CleanPresentationProof`` from the actual durable Study
attempt and its server-side saved terminal/review receipts. The values here are
claims to verify against the selected route; constructing them (including a
receipt ID) does not itself prove that storage occurred.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import chess
from tempo_evidence_math import opening_position_key

from .contracts import DecisionOccurrence, PositionDefinition, PositionKey, RouteOption


@dataclass(frozen=True)
class CleanFirstResponse:
    """One actually recorded learner first response in the earlier attempt."""

    ply: int
    definition: PositionDefinition
    actual_uci: str
    assisted: bool
    event_id: str


@dataclass(frozen=True)
class CleanPresentationProof:
    """Detached facts from a completed, durably saved earlier presentation.

    ``moves_uci`` is the ordered move sequence actually played, and
    ``first_responses`` records the exact learner decision occurrences. The
    nonempty receipt identifiers must come from the server's durable records;
    they are not persistence attestations when supplied by an untrusted caller.
    """

    attempt_id: str
    route_id: str
    root_fen: str
    moves_uci: tuple[str, ...]
    attempt_decisions: tuple[DecisionOccurrence, ...]
    first_responses: tuple[CleanFirstResponse, ...]
    source_snapshot_id: str
    source_revision: str
    source_digest: str
    study_day: date
    owner_epoch: str
    terminal_complete: bool
    terminal_receipt_id: str
    review_receipt_id: str


@dataclass(frozen=True)
class CleanPrefixEligibility:
    """Verified prior attempt facts a route planner may use to skip context.

    ``all_decisions_covered`` applies to the selected route through the requested
    target ply, not to later decisions beyond that target.
    """

    attempt_id: str
    shared_move_count: int
    skipped_decisions: tuple[DecisionOccurrence, ...]
    first_uncovered_decision: DecisionOccurrence | None
    target_is_clean: bool
    all_decisions_covered: bool

    @property
    def skipped_positions(self) -> tuple[PositionKey, ...]:
        return tuple(decision.definition.key for decision in self.skipped_decisions)


def verify_clean_prefix(
    route: RouteOption,
    proof: CleanPresentationProof,
    *,
    target_ply: int,
    study_day: date,
    owner_epoch: str,
) -> CleanPrefixEligibility | None:
    """Return prefix permission only for an exact, whole-clean saved attempt.

    No chess legality is inferred here: ``route`` and its source certificate
    come from the Repertoire owner. This checks exact root, ordered moves,
    source identity, current definitions, day/epoch and each response receipt.
    """
    certificate = route.certificate
    if (
        not route.legal_verified
        or not certificate.complete
        or not certificate.current_integrity
        or not proof.attempt_id
        or not proof.route_id
        or not proof.terminal_complete
        or not proof.terminal_receipt_id
        or not proof.review_receipt_id
        or proof.root_fen != route.start_fen
        or proof.source_snapshot_id != certificate.snapshot_id
        or proof.source_revision != certificate.source_revision
        or proof.source_digest != certificate.source_digest
        or proof.study_day != study_day
        or proof.owner_epoch != owner_epoch
        or not owner_epoch
        or target_ply < 0
    ):
        return None

    shared = 0
    for expected, actual in zip(route.moves_uci, proof.moves_uci):
        if expected != actual:
            break
        shared += 1
    if shared == 0:
        return None

    # Receipt identity must be unambiguous; duplicate/replayed response rows
    # cannot be used to fill a missing learner decision.
    response_by_ply: dict[int, CleanFirstResponse] = {}
    event_ids: set[str] = set()
    for response in proof.first_responses:
        if (
            response.ply in response_by_ply
            or not response.event_id
            or response.event_id in event_ids
        ):
            return None
        response_by_ply[response.ply] = response
        event_ids.add(response.event_id)

    # A whole-clean certificate has one first-response event for every learner
    # decision actually presented in the earlier attempt. The issuer supplies
    # this ordered occurrence list from the saved route/attempt record.
    decision_by_ply: dict[int, DecisionOccurrence] = {}
    board = chess.Board(proof.root_fen)
    learner_turn = chess.WHITE if route.learner_color == "white" else chess.BLACK
    for decision in proof.attempt_decisions:
        if (
            decision.ply in decision_by_ply
            or decision.ply >= len(proof.moves_uci)
            or decision.definition.expected_uci != proof.moves_uci[decision.ply]
        ):
            return None
        decision_by_ply[decision.ply] = decision
        response = response_by_ply.get(decision.ply)
        if (
            response is None
            or response.definition != decision.definition
            or response.actual_uci != decision.definition.expected_uci
            or response.assisted
        ):
            return None
    if set(response_by_ply) != set(decision_by_ply):
        return None

    # Verify that the proof's ordered positions are actually those reached by
    # its ordered move sequence. In particular, a matching FEN string alone
    # cannot stand in for an authored move prefix.
    for ply, uci in enumerate(proof.moves_uci):
        if board.turn == learner_turn:
            decision = decision_by_ply.get(ply)
            try:
                decision_fen = (
                    chess.Board(decision.fen).fen() if decision is not None else None
                )
            except ValueError:
                return None
            if (
                decision is None
                or decision_fen != board.fen()
                or decision.definition.key.position != opening_position_key(board.fen())
            ):
                return None
        elif ply in decision_by_ply:
            return None
        try:
            board.push_uci(uci)
        except (ValueError, chess.IllegalMoveError, chess.InvalidMoveError):
            return None
    if any(ply >= len(proof.moves_uci) for ply in decision_by_ply):
        return None

    skipped: list[DecisionOccurrence] = []
    for decision in route.decisions:
        if decision.ply >= shared or decision.ply >= target_ply:
            continue
        prior = response_by_ply.get(decision.ply)
        if (
            prior is None
            or prior.definition != decision.definition
            or prior.actual_uci != decision.definition.expected_uci
            or prior.assisted
        ):
            return None
        skipped.append(decision)
    if not skipped:
        return None

    # A receipt for a relevant learner decision on the shared played prefix
    # must correspond to a decision on this exact route with the same current
    # definition. This rejects position-only/FEN-only proof shortcuts.
    route_decisions = {d.ply: d for d in route.decisions if d.ply < shared}
    for ply, decision in decision_by_ply.items():
        if ply >= shared:
            continue
        selected = route_decisions.get(ply)
        if selected is None or selected.definition != decision.definition:
            return None
        try:
            if chess.Board(selected.fen).fen() != chess.Board(decision.fen).fen():
                return None
        except ValueError:
            return None

    through_target = tuple(d for d in route.decisions if d.ply <= target_ply)
    if not through_target:
        return None
    uncovered = tuple(d for d in through_target if d.ply >= shared)
    target_is_clean = any(
        d.ply == target_ply and d.ply < shared for d in through_target
    )
    all_decisions_covered = bool(through_target) and not uncovered
    # With no earlier move to skip and no cleanly covered target, the caller
    # should safely fall back to the authored root without using this proof.
    if not skipped and not target_is_clean:
        return None
    return CleanPrefixEligibility(
        proof.attempt_id,
        shared,
        tuple(skipped),
        uncovered[0] if uncovered else None,
        target_is_clean,
        all_decisions_covered,
    )
