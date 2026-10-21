"""Defense analysis/practice contracts."""
from typing import Literal
from pydantic import BaseModel, Field
from .engine import EngineAttemptDiagnostics


class ThreatAnalysisSubmission(BaseModel):
    lease_id: str = Field(min_length=1)
    report: dict
    diagnostics: EngineAttemptDiagnostics | None = None


class ThreatAnalysisFailureRequest(BaseModel):
    lease_id: str = Field(min_length=1)
    error: str = Field(min_length=1, max_length=1000)
    diagnostics: EngineAttemptDiagnostics | None = None


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
