"""Only genuinely common bounded execution and cancellation metadata."""

from typing import Annotated, Literal
from pydantic import Field, model_validator
from ._wire import Model, WireModel, Identifier, Revision, Timestamp


class ExecutionConstraints(Model):
    max_wall_time_ms: Annotated[int, Field(ge=1, le=3600000)]
    max_memory_mb: Annotated[int, Field(ge=1, le=65536)]


class JobRequest(WireModel):
    workspace_id: Identifier
    job_id: Identifier
    request_revision: Revision
    execution_generation: Revision
    input_digest: Identifier
    constraints: ExecutionConstraints


class ResultProvenance(Model):
    input_digest: Identifier
    runtime_name: Identifier
    runtime_version: Identifier
    implementation_version: Identifier
    started_at: Timestamp
    finished_at: Timestamp

    @model_validator(mode="after")
    def chronological_execution(self):
        from datetime import datetime

        if datetime.fromisoformat(
            self.finished_at.replace("Z", "+00:00")
        ) < datetime.fromisoformat(self.started_at.replace("Z", "+00:00")):
            raise ValueError("execution finishes before it starts")
        return self


class JobStatus(WireModel):
    workspace_id: Identifier
    job_id: Identifier
    request_revision: Revision
    execution_generation: Revision
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    retryable: bool
    reason: Identifier | None

    @model_validator(mode="after")
    def retry_semantics(self):
        if self.retryable and self.status != "failed":
            raise ValueError("only failed execution can be retryable")
        if self.status == "failed" and self.reason is None:
            raise ValueError("failure requires reason")
        return self


class CancellationRequest(WireModel):
    workspace_id: Identifier
    job_id: Identifier
    request_revision: Revision
    execution_generation: Revision
    cancellation_id: Identifier


class CancellationAcknowledgement(CancellationRequest):
    status: Literal["requested", "terminated", "already_completed", "stale_generation"]
    acknowledged_at: Timestamp


PUBLIC_ENVELOPES = (JobStatus, CancellationRequest, CancellationAcknowledgement)
