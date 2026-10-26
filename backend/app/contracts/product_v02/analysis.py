"""Supplied analysis values. Providers/evaluators propose; owners decide effects."""
import hashlib
import json
from typing import Annotated, Literal, Protocol

from pydantic import ConfigDict, Field, field_validator, model_validator
from tempo_analysis_jobs_contracts.stockfish import CentipawnScore, MateScore
from tempo_evidence_math import CountEvidence, MoveProbabilityDistribution, PositionMoveUniverse

from . import Digest, Identifier, Revision, UciMove, UtcTimestamp, Value

Probability = Annotated[float, Field(ge=0, le=1)]
Count = Annotated[int, Field(ge=0, le=2**53 - 1)]


class ImmutableCentipawnScore(CentipawnScore):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ImmutableMateScore(MateScore):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class PopulationSelection(Value):
    rating_min: Count
    rating_max: Count
    time_controls: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def explicit_population(self):
        if self.rating_min > self.rating_max or len(set(self.time_controls)) != len(self.time_controls):
            raise ValueError("population requires ordered rating bounds and unique time controls")
        return self


class ProbabilityEvidence(Value):
    interface_version: Literal["0.2.0"] = "0.2.0"
    evidence_id: Identifier
    position: PositionMoveUniverse
    provider: Identifier
    model: Identifier | None
    basis: Literal["population", "personal"]
    population: PopulationSelection | None
    personal_account: Identifier | None
    observed_at: UtcTimestamp | None
    distribution: MoveProbabilityDistribution | None
    counts: CountEvidence | None
    unavailable_reason: Identifier | None

    @model_validator(mode="after")
    def bind_evidence_context(self):
        if (self.basis == "population") != (self.population is not None) or (self.basis == "personal") != (self.personal_account is not None):
            raise ValueError("population selection and personal account are distinct evidence contexts")
        if self.distribution is None:
            if self.unavailable_reason is None:
                raise ValueError("unavailable probabilities require a reason, not invented zeros")
        elif self.distribution.position != self.position or self.unavailable_reason is not None or self.observed_at is None:
            raise ValueError("available probabilities require same exact position and observed date")
        if self.counts is not None and not {move.move_uci for move in self.counts.moves}.issubset(self.position.legal_moves):
            raise ValueError("count evidence contains an illegal move")
        return self


class EngineContext(Value):
    starting_fen: Identifier
    history_uci: tuple[UciMove, ...]
    target_fen: Identifier
    variant: Identifier
    learner_color: Literal["white", "black"]
    engine_version: Identifier
    max_depth: int | None = Field(ge=1, le=128)
    max_nodes: Revision | None
    max_time_ms: Revision | None
    threads: int = Field(ge=1, le=64)
    hash_mb: Revision

    @model_validator(mode="after")
    def bounded_evaluation(self):
        if self.max_depth is None and self.max_nodes is None and self.max_time_ms is None:
            raise ValueError("engine work requires an explicit finite bound")
        return self


class CandidateEvaluation(Value):
    move_uci: UciMove
    score: Annotated[ImmutableCentipawnScore | ImmutableMateScore, Field(discriminator="kind")] | None
    score_perspective: Literal["learner"] = "learner"
    depth: int | None = Field(ge=1, le=128)
    nodes: Count | None
    unavailable_reason: Identifier | None

    @field_validator("score", mode="before")
    @classmethod
    def detach_existing_score(cls, value):
        return value.model_dump(mode="json") if isinstance(value, (CentipawnScore, MateScore)) else value

    @model_validator(mode="after")
    def explicit_missing_score(self):
        if (self.score is None) != (self.unavailable_reason is not None):
            raise ValueError("inconclusive evaluation requires a reason and no invented score")
        return self


class EngineEvidence(Value):
    interface_version: Literal["0.2.0"] = "0.2.0"
    evidence_id: Identifier
    context: EngineContext
    requested_candidates: tuple[UciMove, ...] = Field(min_length=1)
    best: CandidateEvaluation | None
    candidates: tuple[CandidateEvaluation, ...]
    unavailable_reason: Identifier | None

    @model_validator(mode="after")
    def explicit_candidate_coverage(self):
        requested = set(self.requested_candidates)
        evaluated = [item.move_uci for item in self.candidates]
        if len(requested) != len(self.requested_candidates) or len(set(evaluated)) != len(evaluated) or set(evaluated) != requested:
            raise ValueError("every explicit requested candidate needs evidence or an unavailable reason")
        if (self.best is None) != (self.unavailable_reason is not None):
            raise ValueError("missing best evidence must remain explicitly unavailable")
        return self


class RepertoireEdit(Value):
    route_id: Identifier
    position_key: Identifier
    previous_response_uci: UciMove | None
    proposed_response_uci: UciMove
    displayed_pgn: str = Field(min_length=1)


class DisplayedCardChange(Value):
    action: Literal["add", "update", "reuse"]
    presentation_id: Identifier | None
    expected_revision: Revision | None
    starting_fen: Identifier
    moves: tuple[UciMove, ...] = Field(min_length=1)
    learner_color: Literal["white", "black"]

    @model_validator(mode="after")
    def existing_changes_need_identity(self):
        if self.action in {"update", "reuse"} and (self.presentation_id is None or self.expected_revision is None):
            raise ValueError("existing card change requires actual identity/revision")
        return self


class Proposal(Value):
    interface_version: Literal["0.2.0"] = "0.2.0"
    proposal_id: Identifier
    proposal_revision: Revision
    repertoire_id: Identifier
    source_revision: Revision
    source_digest: Digest
    display_digest: Digest
    title: Identifier
    explanation: str = Field(min_length=1)
    edits: tuple[RepertoireEdit, ...]
    card_changes: tuple[DisplayedCardChange, ...]
    engine_evidence: tuple[EngineEvidence, ...]
    supporting_position_keys: tuple[Identifier, ...]
    max_loss_cp: int = Field(default=30, ge=0, le=2**53 - 1)
    threshold_adjustment_reason: Identifier | None = None

    @model_validator(mode="after")
    def explicit_policy_and_support(self):
        if self.max_loss_cp != 30 and self.threshold_adjustment_reason is None:
            raise ValueError("a contextual threshold change must be displayed explicitly")
        if len(set(self.supporting_position_keys)) != len(self.supporting_position_keys):
            raise ValueError("repeated route copies are not independent supporting positions")
        return self

    def content_digest(self) -> str:
        """Bind every displayed field and source revision, excluding the digest itself.

        Repertoire computes this for preview and verifies it on acceptance. This
        is an integrity binding, not authorization or one-own-response validation.
        """
        payload = self.model_dump(mode="json", exclude={"display_digest"})
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return hashlib.sha256(encoded.encode()).hexdigest()


class ProposalDecision(Value):
    proposal_id: Identifier
    expected_proposal_revision: Revision
    expected_source_revision: Revision
    display_digest: Digest
    action: Literal["accept", "edit", "dismiss"]
    edited_proposal: Proposal | None

    @model_validator(mode="after")
    def edit_requires_repreview(self):
        if (self.action == "edit") != (self.edited_proposal is not None):
            raise ValueError("edit supplies a replacement proposal for revalidation/repreview")
        return self

    def matches_preview(self, proposal: Proposal) -> bool:
        return (self.proposal_id == proposal.proposal_id
                and self.expected_proposal_revision == proposal.proposal_revision
                and self.expected_source_revision == proposal.source_revision
                and self.display_digest == proposal.display_digest == proposal.content_digest())


class RepertoireChangeReceipt(Value):
    owner_receipt_id: Identifier
    proposal_id: Identifier
    status: Literal["preview", "accepted", "dismissed", "conflict", "unavailable"]
    source_revision: Revision
    proposal: Proposal | None
    reason: Identifier | None


class RepertoireChangePort(Protocol):
    """Repertoire validates invariants/revisions and atomically applies approval.

    Approval binds all displayed edits and card additions by display_digest.
    edit never publishes; it yields a freshly validated preview for acceptance.
    """
    def preview(self, proposal: Proposal) -> RepertoireChangeReceipt: ...
    def decide(self, decision: ProposalDecision) -> RepertoireChangeReceipt: ...


class RankingCandidate(Value):
    decision_id: Identifier
    repertoire_id: Identifier
    reach_probability: Probability | None
    evidence_support: Count | None
    uncertainty: Probability | None
    personal_encounters: Count | None


class RankingInput(Value):
    repertoire_revisions: tuple[tuple[Identifier, Revision], ...]
    candidates: tuple[RankingCandidate, ...]
    evidence_ids: tuple[Identifier, ...]


class RankedDecision(Value):
    decision_id: Identifier
    rank: Revision
    reason: Identifier


class RankingOutput(Value):
    based_on: RankingInput
    ordered: tuple[RankedDecision, ...]
    unavailable_reason: Identifier | None


class ProbabilityPort(Protocol):
    def evidence(self, position: PositionMoveUniverse, population: PopulationSelection) -> ProbabilityEvidence: ...


class EngineEvaluationPort(Protocol):
    def evaluate(self, context: EngineContext, candidates: tuple[str, ...]) -> EngineEvidence: ...
