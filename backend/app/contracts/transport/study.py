"""Study queue/review/teaching contracts."""
from typing import Literal
from pydantic import BaseModel, Field
from ..opening_evidence import OpeningEvidenceCheckpoint


class QueueAttemptFailureRequest(BaseModel):
    """Displayed content identity for an advisory guided-failure marker."""

    card_id: str | None = Field(default=None, min_length=1)
    expected_revision: int | None = Field(default=None, ge=1)


class PreparedNextIdentity(BaseModel):
    queue_entry_id: int = Field(gt=0)
    card_id: str = Field(min_length=1, max_length=200)
    revision: int = Field(gt=0)
    cycle: int = Field(ge=0)
    local_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class ReviewRequest(BaseModel):
    """Request model for reviewing a card."""

    outcome: Literal["correct", "again"]
    guided: bool = False
    queue_entry_id: int | None = None
    recorded_at: str | None = None
    expected_review_id: int | None = None
    expected_revision: int | None = None
    attempt_id: str | None = Field(default=None, min_length=1, max_length=100)
    opening_evidence_completion: OpeningEvidenceCheckpoint | None = None
    prepared_next: PreparedNextIdentity | None = None

    def durable_payload(self):
        # Adding optional handoff metadata cannot change legacy delivery hashes.
        omitted = {name for name in ("opening_evidence_completion", "prepared_next")
                   if getattr(self, name) is None}
        return self.model_dump(mode="json", exclude=omitted)


class TeachingStateRequest(BaseModel):
    """Request model for the teaching state of a card."""

    revision: int = Field(default=1, ge=1)
    ply: int = Field(ge=0)
