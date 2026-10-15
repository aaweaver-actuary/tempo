from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field
from .opening_evidence_contracts import OpeningEvidenceCheckpoint

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

class TacticAttemptRequest(BaseModel):
    """Request model for attempting a tactic puzzle."""

    attempt_id: str | None = None
    puzzle_id: str
    deck_id: str
    correct: bool
    clean: bool = True
    source_fen: str
    moves: list[str]
    rating: int = 1500

class EndgameTemplateRequest(BaseModel):
    """Request model for creating an endgame template."""

    name: str = Field(min_length=1, max_length=80)
    white_material: str
    black_material: str
    trained_color: Literal["white", "black"]
    goal_mix: Literal["win", "draw", "both"] = "both"

class EndgameProbeRequest(BaseModel):
    """Request model for probing an endgame position."""

    fen: str

class DefenseAttemptRequest(BaseModel):
    attempt_id: str = Field(min_length=1, max_length=100)
    exercise_revision: int = Field(ge=1)
    queue_entry_id: int = Field(ge=1)
    move_uci: str = Field(min_length=4, max_length=5)
    recognition_attempt_id: str | None = Field(default=None, min_length=1, max_length=100)

class DefenseRecognitionRequest(BaseModel):
    attempt_id: str = Field(min_length=1, max_length=100)
    exercise_revision: int = Field(ge=1)
    rubric_version: int | None = Field(default=None, ge=1)
    queue_entry_id: int = Field(ge=1)
    no_concrete_threat: bool = False
    dangerous_piece_square: str | None = Field(default=None, pattern=r"^[a-h][1-8]$")
    destination_square: str | None = Field(default=None, pattern=r"^[a-h][1-8]$")
    king_square: str | None = Field(default=None, pattern=r"^[a-h][1-8]$")
    major_square: str | None = Field(default=None, pattern=r"^[a-h][1-8]$")
    consequence: Literal["checking_fork", "other", "none"]
    hinted: bool = False
