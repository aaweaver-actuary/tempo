"""Source-independent opening evidence. No providers, storage, clocks or policy."""
from __future__ import annotations

from datetime import datetime
import json
import math
import re
from typing import Annotated, Literal, Protocol

import chess
from pydantic import (AfterValidator, BaseModel, ConfigDict, Field, JsonValue,
                      StrictInt, StrictStr, field_validator, model_validator)

from .services.canonical_prefix import position_key

PROBABILITY_TOLERANCE = 1e-9
MAX_EXACT_JSON_INTEGER = 2**53 - 1
Identifier = Annotated[StrictStr, Field(min_length=1, max_length=512)]
UciMove = Annotated[StrictStr, Field(pattern=r"^[a-h][1-8][a-h][1-8][qrbn]?$")]
NonnegativeNumber = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
Count = Annotated[StrictInt, Field(ge=0, le=MAX_EXACT_JSON_INTEGER)]
JsonObject = dict[StrictStr, JsonValue]


def _timestamp(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value):
        raise ValueError("Expected an ISO timestamp with timezone and seconds")
    datetime.fromisoformat(value)
    return value


Timestamp = Annotated[StrictStr, AfterValidator(_timestamp)]


class ContractModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    @field_validator("provenance", check_fields=False)
    @classmethod
    def order_provenance(cls, records):
        return tuple(sorted(records, key=lambda record: (
            record.record_id, record.input_fingerprint, record.references, record.limitations)))

    @model_validator(mode="before")
    @classmethod
    def validate_json_numbers(cls, value):
        def visit(item):
            if isinstance(item, float):
                if not math.isfinite(item):
                    raise ValueError("JSON numbers must be finite")
                if item.is_integer() and abs(item) > MAX_EXACT_JSON_INTEGER:
                    raise ValueError("JSON integers must be exactly representable")
            if type(item) is int and abs(item) > MAX_EXACT_JSON_INTEGER:
                raise ValueError("JSON integers must be exactly representable")
            if isinstance(item, dict):
                for child in item.values():
                    visit(child)
            elif isinstance(item, (list, tuple)):
                for child in item:
                    visit(child)
        visit(value)
        if isinstance(value, dict) and "contract_version" in value and type(value["contract_version"]) is not int:
            raise ValueError("Contract version must be an integer")
        return value


class PositionMoveUniverse(ContractModel):
    fen_key: StrictStr
    legal_moves: tuple[UciMove, ...]

    @model_validator(mode="after")
    def validate_position(self):
        if len(self.fen_key.split()) != 4 or position_key(self.fen_key) != self.fen_key:
            raise ValueError("Expected Tempo's canonical four-field FEN key")
        board = chess.Board(self.fen_key)
        expected_moves = sorted(move.uci() for move in board.legal_moves)
        if not expected_moves:
            raise ValueError("Position has no legal opponent moves")
        if sorted(self.legal_moves) != expected_moves:
            raise ValueError("Legal inventory must contain every legal move exactly once")
        object.__setattr__(self, "legal_moves", tuple(expected_moves))
        return self


def move_universe(fen: str) -> PositionMoveUniverse:
    canonical_key = position_key(fen)
    return PositionMoveUniverse(fen_key=canonical_key,
                                legal_moves=tuple(move.uci() for move in chess.Board(canonical_key).legal_moves))


class MoveProbability(ContractModel):
    move_uci: UciMove
    probability: NonnegativeNumber | None = Field(le=1 + PROBABILITY_TOLERANCE)


class MoveProbabilityDistribution(ContractModel):
    contract_version: Literal[1] = 1
    position: PositionMoveUniverse
    moves: tuple[MoveProbability, ...]
    unknown_mass: NonnegativeNumber = Field(le=1)

    @model_validator(mode="after")
    def validate_mass(self):
        if sorted(item.move_uci for item in self.moves) != list(self.position.legal_moves):
            raise ValueError("Probability table must contain every legal move exactly once")
        known_mass = math.fsum(item.probability for item in self.moves if item.probability is not None)
        if known_mass > 1 + PROBABILITY_TOLERANCE or abs(known_mass + self.unknown_mass - 1) > PROBABILITY_TOLERANCE:
            raise ValueError("Known and unknown probability mass must total one")
        if any(item.probability is None for item in self.moves) and self.unknown_mass <= 0:
            raise ValueError("Unassigned moves require positive unknown mass")
        object.__setattr__(self, "moves", tuple(sorted(self.moves, key=lambda item: item.move_uci)))
        return self


def unknown_distribution(position: PositionMoveUniverse) -> MoveProbabilityDistribution:
    return MoveProbabilityDistribution(position=position, unknown_mass=1,
        moves=tuple(MoveProbability(move_uci=move, probability=None) for move in position.legal_moves))


class MoveCount(ContractModel):
    move_uci: UciMove
    count: Count


class CountEvidence(ContractModel):
    kind: Literal["counts"]
    total_count: Count
    moves: tuple[MoveCount, ...]

    @model_validator(mode="after")
    def validate_counts(self):
        if len({item.move_uci for item in self.moves}) != len(self.moves):
            raise ValueError("Raw evidence contains duplicate moves")
        if sum(item.count for item in self.moves) > self.total_count:
            raise ValueError("Reported counts exceed the sample denominator")
        object.__setattr__(self, "moves", tuple(sorted(self.moves, key=lambda item: item.move_uci)))
        return self


class MoveWeight(ContractModel):
    move_uci: UciMove
    value: NonnegativeNumber


class ScoreEvidence(ContractModel):
    kind: Literal["scores"]
    basis: Literal["probability", "weight"]
    moves: tuple[MoveWeight, ...]

    @model_validator(mode="after")
    def validate_probabilities(self):
        if len({item.move_uci for item in self.moves}) != len(self.moves):
            raise ValueError("Raw evidence contains duplicate moves")
        if self.basis == "probability" and math.fsum(item.value for item in self.moves) > 1 + PROBABILITY_TOLERANCE:
            raise ValueError("Raw probability mass cannot exceed one")
        object.__setattr__(self, "moves", tuple(sorted(self.moves, key=lambda item: item.move_uci)))
        return self


class MethodDescriptor(ContractModel):
    id: Identifier
    version: Identifier
    parameters: JsonObject


class ProvenanceRecord(ContractModel):
    record_id: Identifier
    input_fingerprint: Identifier
    references: tuple[Identifier, ...]
    limitations: tuple[Identifier, ...]

    @field_validator("references", "limitations")
    @classmethod
    def order_sets(cls, values):
        return tuple(sorted(set(values)))


class EvidenceQuality(ContractModel):
    effective_sample_size: NonnegativeNumber | None
    flags: tuple[Identifier, ...]
    uncertainty: JsonObject | None

    @field_validator("flags")
    @classmethod
    def order_flags(cls, values):
        return tuple(sorted(set(values)))


class EvidenceFreshness(ContractModel):
    as_of: Timestamp
    valid_until: Timestamp | None
    state: Literal["fresh", "stale", "unknown"]

    @model_validator(mode="after")
    def validate_freshness(self):
        expected_state = "unknown" if self.valid_until is None else (
            "stale" if datetime.fromisoformat(self.as_of) >= datetime.fromisoformat(self.valid_until) else "fresh")
        if self.state != expected_state:
            raise ValueError("Freshness must match the supplied clock and expiry")
        return self


class OpeningMoveEvidence(ContractModel):
    contract_version: Literal[1] = 1
    evidence_id: Identifier
    derived_from: tuple[Identifier, ...] = ()
    source_id: Identifier
    source_generation: Identifier
    source_version: Identifier | None
    cohort: JsonObject
    raw_evidence: Annotated[CountEvidence | ScoreEvidence, Field(discriminator="kind")]
    distribution: MoveProbabilityDistribution
    normalization: MethodDescriptor | None
    source_timestamp: Timestamp | None
    captured_at: Timestamp
    freshness: EvidenceFreshness
    quality: EvidenceQuality
    provenance: tuple[ProvenanceRecord, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_evidence(self):
        if self.evidence_id in self.derived_from:
            raise ValueError("Evidence cannot derive from itself")
        object.__setattr__(self, "derived_from", tuple(sorted(set(self.derived_from))))
        moves = [item.move_uci for item in self.raw_evidence.moves]
        if len(moves) != len(set(moves)) or not set(moves).issubset(self.distribution.position.legal_moves):
            raise ValueError("Raw evidence contains duplicate or illegal moves")
        if self.normalization is None and (self.distribution.unknown_mass != 1 or any(
                item.probability is not None for item in self.distribution.moves)):
            raise ValueError("Raw-only evidence cannot assign predictive probabilities")
        return self


class OpeningMoveEvidenceBundle(ContractModel):
    contract_version: Literal[1] = 1
    position: PositionMoveUniverse
    target_context: JsonObject
    sources: tuple[OpeningMoveEvidence, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_sources(self):
        source_ids = [source.evidence_id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("Duplicate source evidence ID")
        if any(source.distribution.position != self.position for source in self.sources):
            raise ValueError("Source positions must match the bundle")
        object.__setattr__(self, "sources", tuple(sorted(self.sources, key=lambda source: source.evidence_id)))
        return self


class FusionRequest(ContractModel):
    contract_version: Literal[1] = 1
    evidence: OpeningMoveEvidenceBundle
    policy: MethodDescriptor


class SourceContribution(ContractModel):
    evidence_id: Identifier
    weight: NonnegativeNumber | None
    effective_sample_size: NonnegativeNumber | None


class FusedMoveDistribution(ContractModel):
    contract_version: Literal[1] = 1
    position: PositionMoveUniverse
    target_context: JsonObject
    policy: MethodDescriptor
    status: Literal["available", "unavailable"]
    distribution: MoveProbabilityDistribution | None
    unavailable_reason: Identifier | None
    contributions: tuple[SourceContribution, ...]
    freshness: EvidenceFreshness
    quality: EvidenceQuality
    provenance: tuple[ProvenanceRecord, ...]

    @model_validator(mode="after")
    def validate_result(self):
        if self.status == "available":
            if (self.distribution is None or self.distribution.position != self.position
                    or self.unavailable_reason is not None or not self.contributions
                    or not any((item.probability or 0) > 0 for item in self.distribution.moves)):
                raise ValueError("Available fusion requires assigned mass and source contributions")
        elif self.distribution is not None or self.unavailable_reason is None:
            raise ValueError("Unavailable fusion requires a reason and no distribution")
        contribution_ids = [item.evidence_id for item in self.contributions]
        if len(contribution_ids) != len(set(contribution_ids)):
            raise ValueError("Duplicate source contribution")
        object.__setattr__(self, "contributions", tuple(sorted(self.contributions, key=lambda item: item.evidence_id)))
        return self


class OpeningMoveFusionPolicy(Protocol):
    def __call__(self, request: FusionRequest) -> FusedMoveDistribution: ...


def fuse_move_evidence(request: FusionRequest, policy: OpeningMoveFusionPolicy) -> FusedMoveDistribution:
    """Validate a supplied strategy and carry the exact contributing lineage."""
    policy_input = FusionRequest.model_validate(request.model_dump())
    result = FusedMoveDistribution.model_validate(policy(policy_input).model_dump())
    if (result.position != request.evidence.position or result.target_context != request.evidence.target_context
            or result.policy != request.policy):
        raise ValueError("Fusion result does not match its request")
    source_by_id = {source.evidence_id: source for source in request.evidence.sources}
    if any(item.evidence_id not in source_by_id for item in result.contributions):
        raise ValueError("Unknown source contribution")
    lineage = [record for item in result.contributions for record in source_by_id[item.evidence_id].provenance]
    distinct_records = {serialize_contract(record): record.model_dump() for record in (*lineage, *result.provenance)}
    return FusedMoveDistribution.model_validate({**result.model_dump(), "provenance": list(distinct_records.values())})


def normalize_evidence_weights(evidence: OpeningMoveEvidence, weights: tuple[MoveWeight, ...], *,
                               evidence_id: str, unknown_mass: float, method: MethodDescriptor) -> OpeningMoveEvidence:
    """Allocate only caller-supplied weights/residual; never infer a prior."""
    checked_weights = tuple(MoveWeight.model_validate(item.model_dump()) for item in weights)
    if evidence_id == evidence.evidence_id or evidence_id in evidence.derived_from:
        raise ValueError("Normalization requires a distinct derived evidence ID")
    weights_by_move = {item.move_uci: item.value for item in checked_weights}
    legal_moves = evidence.distribution.position.legal_moves
    if len(weights_by_move) != len(checked_weights) or not set(weights_by_move).issubset(legal_moves):
        raise ValueError("Weights contain duplicate or illegal moves")
    if not checked_weights or max(weights_by_move.values()) <= 0:
        raise ValueError("Cannot normalize empty or zero weights")
    if isinstance(unknown_mass, bool) or not isinstance(unknown_mass, (int, float)) or not math.isfinite(unknown_mass) or not 0 <= unknown_mass <= 1:
        raise ValueError("Unknown mass must be finite and between zero and one")
    largest_weight = max(weights_by_move.values())
    scaled_total = math.fsum(value / largest_weight for value in weights_by_move.values())
    normalized_moves = [dict(move_uci=move, probability=(
        (weights_by_move[move] / largest_weight) / scaled_total * (1 - unknown_mass)
        if move in weights_by_move else None)) for move in legal_moves]
    distribution = MoveProbabilityDistribution(position=evidence.distribution.position,
                                                 moves=normalized_moves, unknown_mass=unknown_mass)
    return OpeningMoveEvidence.model_validate({**evidence.model_dump(), "evidence_id": evidence_id,
                                               "derived_from": (*evidence.derived_from, evidence.evidence_id),
                                               "distribution": distribution.model_dump(),
                                               "normalization": method.model_dump()})


def empirical_frequencies(evidence: CountEvidence) -> dict[str, float | None]:
    """Only reported observations. A zero denominator never divides or fabricates."""
    return {item.move_uci: item.count / evidence.total_count if evidence.total_count else None
            for item in sorted(evidence.moves, key=lambda item: item.move_uci)}


class CoverageMassSummary(ContractModel):
    known_covered_mass: NonnegativeNumber
    known_uncovered_mass: NonnegativeNumber
    unknown_mass: NonnegativeNumber


def coverage_mass_summary(distribution: MoveProbabilityDistribution, covered_moves: set[str]) -> CoverageMassSummary:
    if not covered_moves.issubset(distribution.position.legal_moves):
        raise ValueError("Coverage contains illegal moves")
    return CoverageMassSummary(
        known_covered_mass=math.fsum(item.probability for item in distribution.moves
                                   if item.probability is not None and item.move_uci in covered_moves),
        known_uncovered_mass=math.fsum(item.probability for item in distribution.moves
                                     if item.probability is not None and item.move_uci not in covered_moves),
        unknown_mass=distribution.unknown_mass)


def serialize_contract(value: ContractModel) -> str:
    """Authoritative JSON: set-like collections ordered, context sequences intact."""
    return json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False)
