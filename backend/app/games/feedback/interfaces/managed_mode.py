"""Managed Games mode facts; importing starts no process, connection or reader."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.contracts.product_v02 import Digest, Identifier, Revision, UtcTimestamp, Value

ReleaseRevision = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
ImageIdentity = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class ManagedGamesRuntime(Value):
    service: Literal["api", "foreground-worker", "game-feedback-worker"]
    container_id: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    hostname: Annotated[str, Field(pattern=r"^[0-9a-f]{12}$")]
    boot_nonce: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    image: ImageIdentity

    @model_validator(mode="after")
    def actual_default_docker_identity(self):
        if not self.container_id.startswith(self.hostname):
            raise ValueError("managed runtime hostname must identify its verified container")
        return self


class ManagedGamesModeIntent(Value):
    """Small durable mirror of the existing operator's release/operation receipt."""
    schema_version: Literal[1] = 1
    workspace_id: Identifier
    generation: Revision
    mode: Literal["collect-only", "feedback"]
    phase: Literal["inactive", "transitioning", "active"]
    operation_id: Identifier
    release_revision: ReleaseRevision
    collector_image: ImageIdentity
    configuration_fingerprint: Digest
    target_fingerprint: Digest
    runtimes: tuple[ManagedGamesRuntime, ...] = Field(max_length=3)
    updated_at: UtcTimestamp
    verified_at: UtcTimestamp | None

    @model_validator(mode="after")
    def only_verified_active_intent(self):
        services = {item.service for item in self.runtimes}
        if len(services) != len(self.runtimes) or len({item.hostname for item in self.runtimes}) != len(self.runtimes):
            raise ValueError("managed runtime service and container identities must be distinct")
        if self.phase == "active":
            if self.verified_at is None or "game-feedback-worker" not in services:
                raise ValueError("active mode needs actual verified worker evidence")
            worker = next(item for item in self.runtimes if item.service == "game-feedback-worker")
            if worker.image != self.collector_image:
                raise ValueError("verified worker must use the recorded release image")
            if self.mode == "feedback" and services != {"api", "foreground-worker", "game-feedback-worker"}:
                raise ValueError("feedback intent must bind the actual reader, command worker and collector")
        elif self.verified_at is not None or self.runtimes:
            raise ValueError("inactive/transitioning intent cannot claim current verified runtimes")
        return self

    def admits(self, runtime_id: str) -> bool:
        return bool(self.mode == "feedback" and self.phase == "active" and self.verified_at is not None
                    and any(item.hostname + ":" + item.boot_nonce == runtime_id for item in self.runtimes))


class ManagedModeAdmissionDenied(ValueError):
    """Finite safe reason; no container/private source details in error text."""
    reason = "managed_feedback_mode_not_active"

    def __init__(self):
        super().__init__(self.reason)


class FeedbackReleaseCapabilities(Value):
    """Composition's actual wired release facts, never activation authority alone."""
    policy_version: Literal["first_departure_v1"]
    receipt_interface_version: Literal["0.2.2"]
    selector: bool
    current_authority: bool
    again: bool
    canonical_introduction: bool
    retained_qualification: bool
    foreground_handoff: bool
    issuance_fence: bool
