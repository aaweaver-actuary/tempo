"""Content card definition/prefix split contracts."""
from typing import Literal
from pydantic import BaseModel, Field


class CardRevisionRequest(BaseModel):
    """Request model for revising a card."""

    starting_fen: str
    moves: list[str]
    history_mode: Literal["preserve", "reset"]
    title: str = "Corrected card"
    source_fen: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)


class PrefixSplitRequest(BaseModel):
    """Decide on a one-decision prefix split at the observed card revision."""

    expected_revision: int = Field(ge=1)


class PrefixSplitCard(BaseModel):
    """One card produced by a prefix split."""

    card_id: str
    starting_fen: str
    moves: list[str]
    tested_player_moves: int = Field(ge=1)


class PrefixSplitResponse(BaseModel):
    """Preview or result of splitting a long opening prefix."""

    source_card_id: str
    source_revision: int
    parent: PrefixSplitCard
    continuation: PrefixSplitCard
    applied: bool
    idempotent: bool = False
    shared_line_count: int = Field(default=1, ge=1)
    shared_repertoire_count: int = Field(default=1, ge=1)
