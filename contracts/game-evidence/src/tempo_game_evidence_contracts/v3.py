"""Explicit legacy-graph evidence. No Content publication/activation is asserted."""
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ._observations import coherent_observations
from ._wire import Identifier, Model, Revision, Timestamp, UciMove, serialize_contract, validate_contract
from .legacy import LegacyAuthorityToken
from .v1 import Encounter, RealGameMiss
from .v2 import EvidenceAcknowledgement as V2Acknowledgement, EvidenceWithdrawal, MAX_BATCH_ITEMS

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class LegacyGameSource(Model):
    provider: Identifier
    provider_game_id: Identifier
    content_hash: Digest
    raw_source_digest: Digest


class Comparison(Model):
    encounter_id: Identifier
    card_id: Identifier
    card_revision: Revision
    decision_id: Identifier
    expected_moves: list[UciMove] = Field(min_length=1, max_length=20)
    status: Literal["matched", "missed", "unknown"]
    route_id: Identifier
    route_ply: int = Field(ge=0, le=1000)
    exercise_ply: int = Field(ge=0, le=39)
    executable_digest: Digest


class EvidenceObligation(Model):
    obligation_id: Identifier
    obligation_revision: Revision
    source_evidence_id: Identifier
    source_revision: Revision
    card_id: Identifier
    card_revision: Revision
    decision_id: Identifier
    encounter_id: Identifier
    miss_id: Identifier
    kind: Literal["real_game_miss"]


class EvidenceDelivery(Model):
    contract_version: Literal[3]
    workspace_id: Identifier
    delivery_id: Identifier
    evidence_id: Identifier
    evidence_revision: Revision
    game_id: Identifier
    game_revision: Revision
    occurred_at: Timestamp
    authority: LegacyAuthorityToken
    capture_digest: Digest
    captured_at: Timestamp
    game_source: LegacyGameSource
    encounters: list[Encounter] = Field(max_length=MAX_BATCH_ITEMS)
    comparisons: list[Comparison] = Field(max_length=MAX_BATCH_ITEMS)
    misses: list[RealGameMiss] = Field(max_length=MAX_BATCH_ITEMS)
    obligations: list[EvidenceObligation] = Field(max_length=MAX_BATCH_ITEMS)
    withdrawals: list[EvidenceWithdrawal] = Field(max_length=MAX_BATCH_ITEMS)

    @model_validator(mode="after")
    def coherent_legacy_observations(self):
        if self.authority.workspace_id != self.workspace_id or self.authority.graph_state != "ready":
            raise ValueError("legacy authority must identify this workspace and a ready observed graph")
        if not self.obligations and not self.withdrawals:
            raise ValueError("legacy delivery requires an explicit miss obligation or withdrawal")
        return coherent_observations(self, lambda item: (item.card_revision,))


class EvidenceAcknowledgement(V2Acknowledgement):
    contract_version: Literal[3]


PUBLIC_MODELS = (EvidenceDelivery, EvidenceAcknowledgement)
__all__ = ["EvidenceDelivery", "EvidenceAcknowledgement", "EvidenceObligation", "EvidenceWithdrawal",
           "LegacyAuthorityToken", "LegacyGameSource", "Encounter", "Comparison", "RealGameMiss",
           "PUBLIC_MODELS", "serialize_contract", "validate_contract", "MAX_BATCH_ITEMS"]
