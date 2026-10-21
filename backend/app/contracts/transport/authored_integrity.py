"""Authored integrity/coverage contracts."""
from pydantic import BaseModel, Field


class IntegrityResolutionRequest(BaseModel):
    """Request for choosing the single response at an integrity issue."""

    signature: str
    selected_move_uci: str = Field(pattern=r"^[a-h][1-8][a-h][1-8][qrbn]?$", min_length=4, max_length=5)


class CoverageMaiaMove(BaseModel):
    move_uci: str = Field(pattern=r"^[a-h][1-8][a-h][1-8][qrbn]?$", min_length=4, max_length=5)
    probability: float = Field(ge=0, le=1)


class CoverageMaiaSubmission(BaseModel):
    node_id: str
    lease_id: str
    moves: list[CoverageMaiaMove]
