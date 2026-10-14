"""Advisory publications have no authority over admission or review effects."""

from typing import Annotated, Literal
from pydantic import Field, model_validator
from ._wire import (
    Model,
    WireModel,
    Identifier,
    Revision,
    Timestamp,
    Probability,
    UciMove,
    serialize_contract,
    validate_contract,
)


class Provenance(Model):
    source_id: Identifier
    source_revision: Revision
    input_digest: Identifier
    method_id: Identifier
    method_version: Identifier
    limitations: list[Identifier]


class MoveProbability(Model):
    move: UciMove
    probability: Probability | None


class ProbabilityEstimate(Model):
    kind: Literal["probabilities"]
    position_key: Identifier
    moves: list[MoveProbability] = Field(min_length=1)
    unknown_mass: Probability

    @model_validator(mode="after")
    def honest_mass(self):
        if len({item.move for item in self.moves}) != len(self.moves):
            raise ValueError("duplicate move")
        total = sum(
            item.probability for item in self.moves if item.probability is not None
        )
        if abs(total + self.unknown_mass - 1) > 1e-9:
            raise ValueError("known and unknown mass must total one")
        if (
            any(item.probability is None for item in self.moves)
            and self.unknown_mass <= 0
        ):
            raise ValueError("unknown probability requires unknown mass")
        return self


class RankedCard(Model):
    card_id: Identifier
    value: Annotated[float, Field(allow_inf_nan=False)]


class PolicyRanking(Model):
    kind: Literal["ranking"]
    policy_id: Identifier
    policy_revision: Revision
    cards: list[RankedCard]

    @model_validator(mode="after")
    def distinct_cards(self):
        if len({item.card_id for item in self.cards}) != len(self.cards):
            raise ValueError("ranking contains duplicate cards")
        return self


class ReadinessValuation(Model):
    kind: Literal["readiness_valuation"]
    decision_id: Identifier
    readiness: Probability | None
    valuation: Annotated[float, Field(allow_inf_nan=False)] | None


class CoverageRecommendation(Model):
    kind: Literal["coverage_recommendation"]
    policy_id: Identifier
    known_covered_mass: Probability
    known_uncovered_mass: Probability
    unknown_mass: Probability
    recommended_card_ids: list[Identifier]
    evidence_ids: list[Identifier] = Field(min_length=1)

    @model_validator(mode="after")
    def coverage_mass(self):
        if (
            abs(
                self.known_covered_mass
                + self.known_uncovered_mass
                + self.unknown_mass
                - 1
            )
            > 1e-9
        ):
            raise ValueError("coverage mass must total one")
        return self


Advisory = Annotated[
    ProbabilityEstimate | PolicyRanking | ReadinessValuation | CoverageRecommendation,
    Field(discriminator="kind"),
]


class AdvisoryPublication(WireModel):
    workspace_id: Identifier
    publication_id: Identifier
    publication_revision: Revision
    content_publication_id: Identifier
    content_publication_generation: Revision
    based_on_learning_revision: Revision
    produced_at: Timestamp
    valid_until: Timestamp | None
    provenance: list[Provenance] = Field(min_length=1)
    advisory: Advisory

    @model_validator(mode="after")
    def ordered_freshness(self):
        from datetime import datetime

        if self.valid_until is not None and datetime.fromisoformat(
            self.valid_until.replace("Z", "+00:00")
        ) <= datetime.fromisoformat(self.produced_at.replace("Z", "+00:00")):
            raise ValueError("expiry must follow publication")
        return self


class IntelligenceAvailability(WireModel):
    workspace_id: Identifier
    status: Literal["available", "unavailable", "retryable_failure"]
    publication: AdvisoryPublication | None
    reason: Identifier | None

    @model_validator(mode="after")
    def honest_availability(self):
        if (self.status == "available") != (self.publication is not None):
            raise ValueError("unavailable intelligence cannot carry fabricated advice")
        if (
            self.publication is not None
            and self.workspace_id != self.publication.workspace_id
        ):
            raise ValueError("publication belongs to another workspace")
        if self.status != "available" and self.reason is None:
            raise ValueError("unavailability requires reason")
        return self


PUBLIC_MODELS = (AdvisoryPublication, IntelligenceAvailability)

__all__ = [
    "Provenance",
    "MoveProbability",
    "ProbabilityEstimate",
    "RankedCard",
    "PolicyRanking",
    "ReadinessValuation",
    "CoverageRecommendation",
    "AdvisoryPublication",
    "IntelligenceAvailability",
    "PUBLIC_MODELS",
    "serialize_contract",
    "validate_contract",
]
