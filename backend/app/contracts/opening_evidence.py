"""Versioned shadow contracts. These types never own a scheduling decision."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpeningManifestDecision(EvidenceModel):
    decision_id: str = Field(min_length=64, max_length=64)
    decision_index: int = Field(ge=0, lt=20)
    move_offset: int = Field(ge=0, lt=40)
    fen: str
    expected_uci: str = Field(pattern=r"^[a-h][1-8][a-h][1-8][qrbn]?$")


class OpeningDecisionManifest(EvidenceModel):
    manifest_version: Literal[1] = 1
    policy_version: Literal[1] = 1
    manifest_id: str = Field(min_length=64, max_length=64)
    presentation_snapshot_id: int = Field(gt=0)
    repertoire_id: str = Field(min_length=1)
    card_id: str = Field(min_length=1)
    card_revision: int = Field(gt=0)
    trained_color: Literal["white", "black"]
    decisions: list[OpeningManifestDecision] = Field(min_length=1, max_length=20)


AssistanceKind = Literal["teaching", "hint", "revealed", "guided", "other"]


class OpeningDecisionEvent(EvidenceModel):
    sequence: int = Field(ge=1, le=256)
    decision_index: int = Field(ge=0, lt=20)
    decision_id: str = Field(min_length=64, max_length=64)
    expected_uci: str = Field(pattern=r"^[a-h][1-8][a-h][1-8][qrbn]?$")
    kind: Literal["assistance", "first_response", "manual_failure", "reveal", "correction"]
    observed_at: str
    response_uci: str | None = Field(default=None, pattern=r"^[a-h][1-8][a-h][1-8][qrbn]?$")
    assistance: AssistanceKind | None = None
    disposition: Literal["expected", "alternate", "wrong", "illegal", "unverified"] | None = None

    @model_validator(mode="after")
    def validate_event_fields(self):
        if (self.kind in {"first_response", "correction"}) != (self.response_uci is not None):
            raise ValueError("Only response and correction events contain a submitted move")
        if (self.kind == "assistance") != (self.assistance is not None):
            raise ValueError("Only assistance events contain an assistance category")
        if self.disposition is not None and self.kind != "first_response":
            raise ValueError("Only a first response contains grading context")
        return self


class OpeningAttemptTerminal(EvidenceModel):
    state: Literal["partial", "complete"]
    final_sequence: int = Field(ge=0, le=256)
    ended_at: str


class OpeningEvidenceCheckpoint(EvidenceModel):
    attempt_id: str = Field(min_length=1, max_length=100)
    manifest: OpeningDecisionManifest
    origin_queue_entry_id: int = Field(gt=0)
    queue_entry_id: int | None = Field(default=None, gt=0)
    parent_attempt_id: str | None = Field(default=None, min_length=1, max_length=100)
    started_at: str
    study_timezone: str = Field(min_length=1, max_length=100)
    source: Literal["live", "offline", "reinforcement"] = "live"
    events: list[OpeningDecisionEvent] = Field(default_factory=list, max_length=256)
    terminal: OpeningAttemptTerminal | None = None
