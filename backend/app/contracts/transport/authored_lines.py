"""Authored line/paste contracts."""
from typing import Literal
from pydantic import BaseModel, Field


class BranchRequest(BaseModel):
    """Request model for creating a new analysis branch."""

    repertoire_id: str
    starting_fen: str
    moves: list[str]
    trained_color: Literal["white", "black"]
    name: str = "Analysis branch"
    allow_conflict: bool = False
    source_gap_id: str | None = None


class AnalysisPastePreviewRequest(BaseModel):
    text: str
    starting_fen: str | None = None
    source_gap_id: str | None = None


class AnalysisPasteSelection(BaseModel):
    index: int = Field(ge=0)
    repertoire_id: str
    acknowledge_conflict: bool = False


class AnalysisPasteCommitRequest(AnalysisPastePreviewRequest):
    preview_token: str
    selections: list[AnalysisPasteSelection]


class RemoveBranchRequest(BaseModel):
    """Request model for removing an analysis branch."""

    repertoire_id: str
    starting_fen: str
    moves: list[str]
