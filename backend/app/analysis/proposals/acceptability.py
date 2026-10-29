"""Versioned, exact CP comparison. Mate remains a typed manual-policy finding."""
from dataclasses import dataclass
from typing import Literal

import chess

from app.contracts.product_v02.analysis import CandidateEvaluation, EngineContext, EngineEvidence


@dataclass(frozen=True)
class QualityPolicy:
    version: str = "learner-cp-loss-v1"
    max_loss_cp: int = 30
    adjustment_reason: str | None = None

    def __post_init__(self):
        if (type(self.max_loss_cp) is not int or not 0 <= self.max_loss_cp <= 2**53 - 1
                or not self.version or (self.max_loss_cp != 30 and not self.adjustment_reason)):
            raise ValueError("explicit_quality_policy_required")


@dataclass(frozen=True)
class QualityDecision:
    move_uci: str
    status: Literal["acceptable", "rejected", "inconclusive"]
    reason: str
    loss_cp: int | None
    policy: QualityPolicy


def comparable_scores(context: EngineContext, evidence: EngineEvidence,
                      move_uci: str) -> tuple[CandidateEvaluation | None, str | None]:
    """The shared contract uses learner perspective for both colors.

    #497 maps bounded/partial UCI scores to unavailable, never exact CP evidence.
    Different search depths/settings and missing scores cannot be compared here.
    """
    if evidence.context != context:
        return None, "engine_context_mismatch"
    candidate = next((item for item in evidence.candidates if item.move_uci == move_uci), None)
    best = evidence.best
    if (evidence.unavailable_reason or best is None or candidate is None
            or best.score is None or candidate.score is None
            or best.unavailable_reason or candidate.unavailable_reason):
        return candidate, "missing_or_bounded_score"
    board = chess.Board(context.target_fen)
    if any(chess.Move.from_uci(item.move_uci) not in board.legal_moves for item in (best, candidate)):
        return candidate, "illegal_engine_move"
    if best.score.kind != "centipawns" or candidate.score.kind != "centipawns":
        return candidate, "mate_requires_manual_policy"
    if best.depth is None or best.depth != candidate.depth:
        return candidate, "incomparable_depth"
    return candidate, None


def assess(context: EngineContext, evidence: EngineEvidence, move_uci: str,
           policy: QualityPolicy = QualityPolicy()) -> QualityDecision:
    candidate, reason = comparable_scores(context, evidence, move_uci)
    if reason:
        return QualityDecision(move_uci, "inconclusive", reason, None, policy)
    loss = evidence.best.score.value - candidate.score.value
    if loss < 0:
        return QualityDecision(move_uci, "inconclusive", "candidate_exceeds_best_baseline", loss, policy)
    return QualityDecision(move_uci, "acceptable" if loss <= policy.max_loss_cp else "rejected",
                           "within_cp_tolerance" if loss <= policy.max_loss_cp else "exceeds_cp_tolerance",
                           loss, policy)
