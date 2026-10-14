"""Content publishes immutable definitions; Learning accepts and activates."""

from typing import Literal
from pydantic import Field, model_validator
from ._wire import (
    WireModel,
    Identifier,
    Revision,
    Timestamp,
    serialize_contract,
    validate_contract,
)
from ._exercise import ExecutableExercise


class Publication(WireModel):
    workspace_id: Identifier
    publication_id: Identifier
    source_revision: Revision
    publication_generation: Revision
    built_at: Timestamp
    definitions: list[ExecutableExercise] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def immutable_lineage(self):
        if len({item.card_id for item in self.definitions}) != len(self.definitions):
            raise ValueError("duplicate card identity")
        if any(
            (item.publication_id, item.publication_generation, item.source_revision)
            != (self.publication_id, self.publication_generation, self.source_revision)
            for item in self.definitions
        ):
            raise ValueError("definitions must belong to this publication")
        return self


class PublicationValidation(WireModel):
    workspace_id: Identifier
    publication_id: Identifier
    publication_generation: Revision
    status: Literal["validated", "rejected", "retryable_failure"]
    errors: list[Identifier]

    @model_validator(mode="after")
    def validation_errors(self):
        if (self.status == "validated") != (not self.errors):
            raise ValueError("validation status must agree with errors")
        return self


class PublicationAcceptance(WireModel):
    workspace_id: Identifier
    publication_id: Identifier
    publication_generation: Revision
    status: Literal["accepted_pending", "rejected", "retryable_failure"]
    reason: Identifier | None

    @model_validator(mode="after")
    def actionable_rejection(self):
        if self.status != "accepted_pending" and self.reason is None:
            raise ValueError("failure requires reason")
        return self


class ActivationRequest(WireModel):
    workspace_id: Identifier
    activation_id: Identifier
    publication_id: Identifier
    publication_generation: Revision
    expected_activation_revision: Revision


class ActivationAcknowledgement(WireModel):
    workspace_id: Identifier
    activation_id: Identifier
    publication_id: Identifier
    publication_generation: Revision
    activation_revision: Revision
    status: Literal[
        "accepted_pending",
        "activated",
        "rejected",
        "retryable_failure",
        "stale_generation",
    ]
    reason: Identifier | None

    @model_validator(mode="after")
    def actionable_activation(self):
        if (
            self.status in ("rejected", "retryable_failure", "stale_generation")
            and self.reason is None
        ):
            raise ValueError("failure requires reason")
        return self


class RetirementRequest(WireModel):
    workspace_id: Identifier
    retirement_id: Identifier
    publication_id: Identifier
    publication_generation: Revision
    expected_activation_revision: Revision
    replacement_publication_id: Identifier | None
    replacement_generation: Revision | None

    @model_validator(mode="after")
    def replacement_identity(self):
        if (self.replacement_publication_id is None) != (
            self.replacement_generation is None
        ):
            raise ValueError("replacement requires both identity and generation")
        if (self.replacement_publication_id, self.replacement_generation) == (
            self.publication_id,
            self.publication_generation,
        ):
            raise ValueError("cannot replace a publication with itself")
        return self


class RetirementAcknowledgement(WireModel):
    workspace_id: Identifier
    retirement_id: Identifier
    publication_id: Identifier
    publication_generation: Revision
    activation_revision: Revision
    status: Literal[
        "accepted_pending",
        "retired",
        "rejected",
        "retryable_failure",
        "stale_generation",
    ]
    stale_attempts_fenced: bool
    reason: Identifier | None

    @model_validator(mode="after")
    def retirement_fenced(self):
        if (
            self.status in ("rejected", "retryable_failure", "stale_generation")
            and self.reason is None
        ):
            raise ValueError("failure requires reason")
        if (self.status == "retired") != self.stale_attempts_fenced:
            raise ValueError("retirement completes only after authoritative fencing")
        return self


PUBLIC_MODELS = (
    Publication,
    PublicationValidation,
    PublicationAcceptance,
    ActivationRequest,
    ActivationAcknowledgement,
    RetirementRequest,
    RetirementAcknowledgement,
)

__all__ = [
    "Publication",
    "PublicationValidation",
    "PublicationAcceptance",
    "ActivationRequest",
    "ActivationAcknowledgement",
    "RetirementRequest",
    "RetirementAcknowledgement",
    "PUBLIC_MODELS",
    "serialize_contract",
    "validate_contract",
    "ExecutableExercise",
]
