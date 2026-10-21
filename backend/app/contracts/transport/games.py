"""Games read contracts."""
from typing import Literal
from pydantic import BaseModel, Field
from .game_sync import GameProvider


class GameTimelineEvent(BaseModel):
    """One public event on a game's repertoire/analysis timeline."""

    ply: int = Field(ge=0)
    kind: str


class GamePublicRecord(BaseModel):
    """Allowlisted game transport record; SQLite bookkeeping stays private."""

    id: str
    provider: GameProvider
    username: str
    played_at: str
    speed: str
    rated: Literal[0, 1]
    color: Literal["white", "black"]
    result: str
    start_fen: str
    moves: list[str]
    game_url: str | None = None
    opening_name: str | None = None
    analysis_state: Literal["pending", "analyzing", "ready", "complete", "failed"]
    analysis_version: int = Field(ge=0)
    major_mistake_ply: int | None = None
    missed_punishment_ply: int | None = None
    repertoire_id: str | None = None
    classification: str | None = None
    divergence_ply: int | None = None
    divergence_fen: str | None = None
    expected: list[str] = Field(default_factory=list)
    actual_uci: str | None = None
    deviation_card_id: str | None = None
    matched_player_decisions: int | None = None
    repertoire_opportunities: int | None = None
    deepest_covered_ply: int | None = None
    first_opponent_gap_ply: int | None = None
    out_of_book_ply: int | None = None
    timeline: list[GameTimelineEvent] = Field(default_factory=list)
    adherence: float | None = None


class GamesSummaryResponse(BaseModel):
    """Typed response for the Games library."""

    total: int = Field(ge=0)
    games: list["GameSummaryRecord"]
    next_cursor: str | None = None
    aggregates: dict[str, int] = Field(default_factory=dict)


class GameSummaryRecord(BaseModel):
    """Lightweight library row. Moves and evidence are fetched by game ID."""

    id: str
    provider: GameProvider
    played_at: str
    speed: str
    color: Literal["white", "black"]
    result: str
    opening_name: str | None = None
    analysis_state: Literal["pending", "analyzing", "ready", "complete", "failed"]
    major_mistake_ply: int | None = None
    missed_punishment_ply: int | None = None
    repertoire_id: str | None = None
    classification: str | None = None
    divergence_ply: int | None = None
    matched_player_decisions: int | None = None
    repertoire_opportunities: int | None = None
    adherence: float | None = None
    matched_position_ply: int | None = None
