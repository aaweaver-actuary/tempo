"""Authored repertoire settings/annotation contracts."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class RepertoireSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_cards_per_day: int | None = Field(ge=0, le=100, strict=True)


class RepertoireSettingsResponse(BaseModel):
    repertoire_id: str
    new_cards_per_day: int | None = Field(ge=0, le=100)
    effective_new_cards_per_day: int = Field(ge=0, le=100)


class RepertoireRenameRequest(BaseModel):
    """Request model for renaming a repertoire."""

    name: str = Field(min_length=1, max_length=80)


class AnnotationArrow(BaseModel):
    """Model representing an annotation arrow on a chessboard."""

    from_square: str = Field(alias="from", pattern=r"^[a-h][1-8]$")
    to: str = Field(pattern=r"^[a-h][1-8]$")
    color: Literal["green", "red", "blue", "yellow"] = "green"


class AnnotationSquare(BaseModel):
    """Model representing an annotation square on a chessboard."""

    square: str = Field(pattern=r"^[a-h][1-8]$")
    color: Literal["green", "red", "blue", "yellow"] = "green"


class PositionAnnotationRequest(BaseModel):
    """Request model for annotating a position on the chessboard."""

    fen: str
    comment: str = Field(default="", max_length=4000)
    arrows: list[AnnotationArrow] = Field(default_factory=list)
    squares: list[AnnotationSquare] = Field(default_factory=list)
