"""Endgames contracts."""
from typing import Literal
from pydantic import BaseModel, Field


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
