"""Common opponent errors: explicit frequency plus separate engine criterion."""
from __future__ import annotations

from dataclasses import dataclass, replace
import math

from tempo_evidence_math import move_universe

from app.contracts.product_v02.analysis import (
    EngineContext, EngineEvidence, EngineEvaluationPort, PopulationSelection,
    ProbabilityEvidence, ProbabilityPort,
)
from .acceptability import comparable_scores
from .coordinator import ProposalCoordinator, context_board
from .ports import CapabilityUnavailable, ProposalResult, ProposalTarget


@dataclass(frozen=True)
class BlunderPolicy:
    # Initial analysis parameters, not an implicit user preference or own-move
    # tolerance. All are recorded/displayed for every emitted preparation.
    version: str = "opponent-error-frequency-cp-v1"
    min_opponent_loss_cp: int = 100
    min_probability: float = 0.05
    min_observed_count: int = 5

    def __post_init__(self):
        if (not self.version or type(self.min_opponent_loss_cp) is not int
                or not 1 <= self.min_opponent_loss_cp <= 2**53 - 1
                or not math.isfinite(self.min_probability) or not 0 <= self.min_probability <= 1
                or type(self.min_observed_count) is not int or self.min_observed_count < 1):
            raise ValueError("invalid_blunder_policy")


@dataclass(frozen=True)
class BlunderRequest:
    response: ProposalTarget
    opponent_context: EngineContext
    opponent_move_uci: str
    population: PopulationSelection
    policy: BlunderPolicy = BlunderPolicy()


@dataclass(frozen=True)
class BlunderResult:
    result: ProposalResult
    policy: BlunderPolicy
    frequency: ProbabilityEvidence | None
    opponent_engine: EngineEvidence | None
    opponent_loss_cp: int | None


def prepare_blunder(request: BlunderRequest, *, probability: ProbabilityPort,
                    opponent_engine: EngineEvaluationPort,
                    coordinator: ProposalCoordinator) -> BlunderResult:
    context, response, policy = request.opponent_context, request.response, request.policy
    board = context_board(context)
    if board.turn == (context.learner_color == "white"):
        raise ValueError("opponent_turn_required")
    board.push_uci(request.opponent_move_uci)
    response_board = context_board(response.context)
    if (response.context.history_uci != context.history_uci + (request.opponent_move_uci,)
            or board.fen() != response_board.fen()
            or context.model_dump(exclude={"target_fen", "history_uci"})
            != response.context.model_dump(exclude={"target_fen", "history_uci"})):
        raise ValueError("opponent_error_does_not_reach_response_context")
    position = move_universe(context.target_fen)
    try:
        frequency = probability.evidence(position, request.population)
    except CapabilityUnavailable:
        return BlunderResult(ProposalResult("inconclusive", "probability_unavailable"), policy, None, None, None)

    def finding(reason, *, status="inconclusive", evidence=None, loss=None):
        return BlunderResult(ProposalResult(status, reason,
            engine_evidence=(evidence,) if evidence is not None else ()),
            policy, frequency, evidence, loss)

    if (frequency.position != position or frequency.basis != "population"
            or frequency.population != request.population or frequency.observed_at is None):
        return finding("frequency_context_mismatch")
    count = next((item.count for item in frequency.counts.moves
                  if item.move_uci == request.opponent_move_uci), None) if frequency.counts else None
    chance = next((item.probability for item in frequency.distribution.moves
                   if item.move_uci == request.opponent_move_uci), None) if frequency.distribution else None
    if chance is None and count is not None and frequency.counts.total_count:
        chance = count / frequency.counts.total_count  # Full raw denominator; never known-mass normalization.
    if chance is None or (frequency.counts is not None and count is None):
        return finding("frequency_unavailable_for_opponent_move")
    if chance < policy.min_probability or (count is not None and count < policy.min_observed_count):
        return finding("opponent_move_not_frequent_under_displayed_policy", status="no_sound_candidate")
    try:
        evidence = opponent_engine.evaluate(context, (request.opponent_move_uci,))
    except CapabilityUnavailable:
        return finding("opponent_engine_unavailable")
    if evidence.requested_candidates != (request.opponent_move_uci,):
        return finding("opponent_engine_request_mismatch", evidence=evidence)
    error, reason = comparable_scores(context, evidence, request.opponent_move_uci)
    if reason:
        return finding(reason, evidence=evidence)
    # At the opponent turn, their best move minimizes the learner's score. The
    # error gives the learner a gain. Do not apply the own-move subtraction here.
    loss = error.score.value - evidence.best.score.value
    if loss < policy.min_opponent_loss_cp:
        return finding("opponent_loss_below_separate_blunder_threshold",
                       status="no_sound_candidate", evidence=evidence, loss=loss)
    result = coordinator.propose(replace(response, kind="common_blunder"))
    details = (f"Common-blunder analysis policy {policy.version}: opponent loss {loss} CP "
        f">= {policy.min_opponent_loss_cp} CP; frequency {chance!r} >= {policy.min_probability!r}; "
        f"observed move count {count!r}, required when available {policy.min_observed_count}; "
        f"full denominator {frequency.counts.total_count if frequency.counts else None!r}. "
        f"Provider {frequency.provider}, model {frequency.model!r}, evidence {frequency.evidence_id}, "
        f"observed {frequency.observed_at}, population {frequency.population.model_dump_json()}, "
        f"position {frequency.position.fen_key}; unknown mass "
        f"{frequency.distribution.unknown_mass if frequency.distribution else None!r}. "
        "Opponent-error threshold is separate from the own-response CP tolerance. "
        "This is a preparation candidate, with no recorded game lapse, Again or introduction.")
    engines = (evidence,) + result.engine_evidence
    if result.proposal is not None:
        proposal = result.proposal.model_copy(update={"explanation": result.proposal.explanation + "\n" + details,
                                                     "engine_evidence": engines})
        proposal = proposal.model_copy(update={"display_digest": proposal.content_digest()})
        result = replace(result, proposal=proposal)
    if result.existing_card is not None:
        result = replace(result, existing_card=replace(result.existing_card,
            explanation=result.existing_card.explanation + "\n" + details, engine_evidence=engines))
    return BlunderResult(replace(result, engine_evidence=engines), policy, frequency, evidence, loss)
