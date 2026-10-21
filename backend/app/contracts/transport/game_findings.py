"""Games finding/adaptation contracts."""
from typing import Literal
from pydantic import BaseModel


class GameFindingDecisionRequest(BaseModel):
    """Request model for making a decision on a game finding."""

    decision: Literal["accepted", "ignored"]


class GameExclusionRequest(BaseModel):
    """Request model for excluding a game."""

    excluded: bool


class GameFindingCardRequest(BaseModel):
    """Request model for creating a card from a game finding."""

    save: bool = False
    starting_fen: str | None = None
    moves: list[str] | None = None
    trained_color: Literal["white", "black"] | None = None


class GameFindingCurationRequest(BaseModel):
    """Request for a non-training decision in the tactical curation queue."""

    action: Literal["skip", "ignore"]
