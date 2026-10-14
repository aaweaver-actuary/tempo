"""Observed games and idempotent obligation delivery, never controlled reviews."""

from typing import Literal
from pydantic import Field, model_validator
from ._wire import (
    Model,
    WireModel,
    Identifier,
    Revision,
    Count,
    Timestamp,
    UciMove,
    serialize_contract,
    validate_contract,
)


class GameProvenance(Model):
    provider: Identifier
    provider_game_id: Identifier
    source_digest: Identifier
    imported_at: Timestamp


class ImportedGame(WireModel):
    workspace_id: Identifier
    game_id: Identifier
    game_revision: Revision
    provenance: GameProvenance
    starting_fen: Identifier
    moves: list[UciMove] = Field(max_length=1000)
    played_at: Timestamp | None
    observed_color: Literal["white", "black"]


class Encounter(Model):
    encounter_id: Identifier
    ply: Count
    position_key: Identifier
    decision_id: Identifier
    observed_move: UciMove


class Comparison(Model):
    encounter_id: Identifier
    card_id: Identifier
    decision_id: Identifier
    publication_id: Identifier
    publication_generation: Revision
    expected_moves: list[UciMove] = Field(min_length=1)
    status: Literal["matched", "missed", "unknown"]


class RealGameMiss(Model):
    miss_id: Identifier
    encounter_id: Identifier
    decision_id: Identifier
    reason: Identifier


class EvidenceObligation(Model):
    obligation_id: Identifier
    source_evidence_id: Identifier
    source_revision: Revision
    card_id: Identifier
    decision_id: Identifier
    publication_generation: Revision
    kind: Literal["real_game_miss", "observed_encounter"]


class EvidenceDelivery(WireModel):
    workspace_id: Identifier
    delivery_id: Identifier
    evidence_id: Identifier
    evidence_revision: Revision
    game_id: Identifier
    game_revision: Revision
    encounters: list[Encounter] = Field(min_length=1)
    comparisons: list[Comparison]
    misses: list[RealGameMiss]
    obligations: list[EvidenceObligation]

    @model_validator(mode="after")
    def coherent_observations(self):
        encounter_ids = {item.encounter_id for item in self.encounters}
        if len(encounter_ids) != len(self.encounters):
            raise ValueError("duplicate encounter")
        if any(
            item.encounter_id not in encounter_ids
            for item in [*self.comparisons, *self.misses]
        ):
            raise ValueError("comparison/miss refers to unknown encounter")
        for items, field in (
            (self.misses, "miss_id"),
            (self.obligations, "obligation_id"),
        ):
            if len({getattr(item, field) for item in items}) != len(items):
                raise ValueError("duplicate evidence identity")
        if any(
            (item.source_evidence_id, item.source_revision)
            != (self.evidence_id, self.evidence_revision)
            for item in self.obligations
        ):
            raise ValueError("obligation must preserve evidence lineage")
        return self


class EvidenceAcknowledgement(WireModel):
    workspace_id: Identifier
    delivery_id: Identifier
    evidence_id: Identifier
    evidence_revision: Revision
    status: Literal[
        "accepted_pending",
        "completed",
        "retryable_failure",
        "terminal_failure",
        "conflict",
        "stale_revision",
    ]
    accepted_obligation_ids: list[Identifier]
    learning_revision: Revision | None
    reason: Identifier | None

    @model_validator(mode="after")
    def completed_authority(self):
        if (
            self.status
            in ("retryable_failure", "terminal_failure", "conflict", "stale_revision")
            and self.reason is None
        ):
            raise ValueError("failure requires reason")
        if (self.status == "completed") != (self.learning_revision is not None):
            raise ValueError(
                "only completed delivery reports committed Learning revision"
            )
        if len(set(self.accepted_obligation_ids)) != len(self.accepted_obligation_ids):
            raise ValueError("duplicate obligation acknowledgement")
        return self


PUBLIC_MODELS = (ImportedGame, EvidenceDelivery, EvidenceAcknowledgement)

__all__ = [
    "GameProvenance",
    "ImportedGame",
    "Encounter",
    "Comparison",
    "RealGameMiss",
    "EvidenceObligation",
    "EvidenceDelivery",
    "EvidenceAcknowledgement",
    "PUBLIC_MODELS",
    "serialize_contract",
    "validate_contract",
]
