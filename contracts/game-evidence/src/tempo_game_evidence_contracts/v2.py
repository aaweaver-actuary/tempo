"""Explicit correction/chronology protocol. V1 remains an additive protocol."""

from typing import Literal

from pydantic import Field, model_validator

from ._wire import Identifier, Model, Revision, Timestamp, UciMove, serialize_contract, validate_contract
from .v1 import Comparison as V1Comparison, Encounter, GameProvenance, RealGameMiss

MAX_BATCH_ITEMS = 128


class WireModel(Model):
    contract_version: Literal[2]


class Comparison(V1Comparison):
    expected_moves: list[UciMove] = Field(min_length=1, max_length=20)


class ImportedGame(WireModel):
    workspace_id: Identifier
    game_id: Identifier
    game_revision: Revision
    provenance: GameProvenance
    starting_fen: Identifier
    moves: list[UciMove] = Field(max_length=1000)
    played_at: Timestamp | None
    observed_color: Literal["white", "black"]


class EvidenceObligation(Model):
    obligation_id: Identifier
    obligation_revision: Revision
    source_evidence_id: Identifier
    source_revision: Revision
    card_id: Identifier
    card_revision: Revision
    decision_id: Identifier
    publication_id: Identifier
    publication_generation: Revision
    encounter_id: Identifier
    miss_id: Identifier | None
    kind: Literal["real_game_miss", "observed_encounter"]


class EvidenceWithdrawal(Model):
    obligation_id: Identifier
    expected_obligation_revision: Revision
    reason: Literal["source_corrected", "source_excluded", "source_deleted", "reanalysis"]


class EvidenceDelivery(WireModel):
    workspace_id: Identifier
    delivery_id: Identifier
    evidence_id: Identifier
    evidence_revision: Revision
    game_id: Identifier
    game_revision: Revision
    occurred_at: Timestamp | None
    encounters: list[Encounter] = Field(max_length=MAX_BATCH_ITEMS)
    comparisons: list[Comparison] = Field(max_length=MAX_BATCH_ITEMS)
    misses: list[RealGameMiss] = Field(max_length=MAX_BATCH_ITEMS)
    obligations: list[EvidenceObligation] = Field(max_length=MAX_BATCH_ITEMS)
    withdrawals: list[EvidenceWithdrawal] = Field(max_length=MAX_BATCH_ITEMS)

    @model_validator(mode="after")
    def coherent_observations(self):
        encounters = {item.encounter_id: item for item in self.encounters}
        misses = {item.miss_id: item for item in self.misses}
        for items, identity in ((self.encounters, "encounter_id"), (self.misses, "miss_id"),
                                (self.obligations, "obligation_id"), (self.withdrawals, "obligation_id")):
            if len({getattr(item, identity) for item in items}) != len(items):
                raise ValueError("duplicate evidence identity")
        if not self.encounters and not self.withdrawals:
            raise ValueError("empty delivery must explicitly withdraw a contribution")
        if {item.obligation_id for item in self.obligations} & {item.obligation_id for item in self.withdrawals}:
            raise ValueError("cannot add and withdraw the same obligation")
        for comparison in self.comparisons:
            encounter = encounters.get(comparison.encounter_id)
            if encounter is None or comparison.decision_id != encounter.decision_id:
                raise ValueError("comparison must identify its encounter decision")
        for miss in self.misses:
            encounter = encounters.get(miss.encounter_id)
            if encounter is None or miss.decision_id != encounter.decision_id:
                raise ValueError("miss must identify its encounter decision")
        for obligation in self.obligations:
            if (obligation.source_evidence_id, obligation.source_revision) != (self.evidence_id, self.evidence_revision):
                raise ValueError("obligation must preserve evidence lineage")
            encounter = encounters.get(obligation.encounter_id)
            if encounter is None or encounter.decision_id != obligation.decision_id:
                raise ValueError("obligation must identify its encounter decision")
            comparisons = [item for item in self.comparisons if
                          (item.encounter_id, item.card_id, item.decision_id, item.publication_id, item.publication_generation)
                          == (obligation.encounter_id, obligation.card_id, obligation.decision_id,
                              obligation.publication_id, obligation.publication_generation)]
            if len(comparisons) != 1:
                raise ValueError("obligation requires one explicit comparison")
            if obligation.kind == "real_game_miss":
                miss = misses.get(obligation.miss_id)
                if (miss is None or (miss.encounter_id, miss.decision_id) !=
                        (obligation.encounter_id, obligation.decision_id) or comparisons[0].status != "missed"):
                    raise ValueError("miss obligation requires its missed comparison and miss")
            elif obligation.miss_id is not None:
                raise ValueError("observed encounter must not claim a miss")
        return self


class EvidenceAcknowledgement(WireModel):
    workspace_id: Identifier
    delivery_id: Identifier
    evidence_id: Identifier
    evidence_revision: Revision
    status: Literal["accepted_pending", "completed", "retryable_failure", "terminal_failure", "conflict", "stale_revision"]
    accepted_obligation_ids: list[Identifier] = Field(
        max_length=MAX_BATCH_ITEMS, json_schema_extra={"uniqueItems": True})
    learning_revision: Revision | None
    reason: Identifier | None

    @model_validator(mode="after")
    def completed_authority(self):
        if (self.status == "completed") != (self.learning_revision is not None):
            raise ValueError("only completed acceptance has a Learning revision")
        if self.status != "completed" and self.reason is None:
            raise ValueError("incomplete delivery requires a reason")
        if self.status != "completed" and self.accepted_obligation_ids:
            raise ValueError("incomplete delivery cannot claim accepted obligations")
        if len(set(self.accepted_obligation_ids)) != len(self.accepted_obligation_ids):
            raise ValueError("duplicate obligation acknowledgement")
        return self


PUBLIC_MODELS = (ImportedGame, EvidenceDelivery, EvidenceAcknowledgement)
__all__ = ["ImportedGame", "EvidenceDelivery", "EvidenceAcknowledgement", "EvidenceObligation",
           "EvidenceWithdrawal", "Encounter", "Comparison", "RealGameMiss", "PUBLIC_MODELS",
           "serialize_contract", "validate_contract", "MAX_BATCH_ITEMS"]
