"""Tactics contracts."""
from typing import Literal
from uuid import UUID
from urllib.parse import urlsplit
from pydantic import BaseModel, Field, model_validator


class TacticCaptureRequest(BaseModel):
    """An authored legal tactic and one stable capture event."""

    capture_id: UUID
    starting_fen: str = Field(min_length=1, max_length=200)
    moves: list[str] = Field(min_length=1, max_length=256)
    source_kind: Literal["puzzle_rush", "game", "manual", "other"] = "manual"
    source_ref: str | None = Field(default=None, max_length=500)
    source_url: str | None = Field(default=None, max_length=2000)
    note: str = Field(default="", max_length=10000)

    @model_validator(mode="after")
    def validate_source_url(self):
        if self.source_url:
            parsed = urlsplit(self.source_url)
            if parsed.scheme not in {"https", "http"} or not parsed.netloc:
                raise ValueError("Source URL must be an http or https URL")
        return self


class TacticCaptureResponse(BaseModel):
    capture_id: str
    card_id: str
    reused: bool
    queued: bool
    introduced: bool


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


class TacticActivationRequest(BaseModel):
    """Request model for activating or deactivating tactic packs."""

    pack_ids: list[str] = Field(min_length=1, max_length=692)
    active: bool
