from typing import Literal

from pydantic import BaseModel, Field


class Settings(BaseModel):
    initial_depth: int = Field(default=6, ge=2, le=20)
    timezone: str = "local"
    new_cards_per_day: int = Field(default=10, ge=0, le=100)
    lichess_username: str = ""
    chesscom_username: str = ""
    auto_sync_minutes: int = Field(default=3, ge=2, le=60)
    engine_line_window_cp: int = Field(default=30, ge=0, le=300)
    major_mistake_cp: int = Field(default=100, ge=25, le=1000)
    light_first_interval_days: int = Field(default=7, ge=1, le=90)
    draw_hold_user_moves: int = Field(default=20, ge=5, le=100)


class ReviewRequest(BaseModel):
    outcome: Literal["correct", "again"]
    guided: bool = False
    queue_entry_id: int | None = None


class BranchRequest(BaseModel):
    repertoire_id: str
    starting_fen: str
    moves: list[str]
    trained_color: Literal["white", "black"]
    name: str = "Analysis branch"


class CardRevisionRequest(BaseModel):
    starting_fen: str
    moves: list[str]
    history_mode: Literal["preserve", "reset"]
    title: str = "Corrected card"
    source_fen: str | None = None


class RepertoireRenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class TacticAttemptRequest(BaseModel):
    puzzle_id: str
    deck_id: str
    correct: bool
    clean: bool = True
    source_fen: str
    moves: list[str]
    rating: int = 1500


class EndgameTemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    white_material: str
    black_material: str
    trained_color: Literal["white", "black"]
    goal_mix: Literal["win", "draw", "both"] = "both"


class EndgameProbeRequest(BaseModel):
    fen: str


class GameAnalysisRequest(BaseModel):
    evaluations: list[dict]
    depth: int = Field(default=13, ge=1, le=40)


class AccountSettings(BaseModel):
    lichess_username: str = ""
    chesscom_username: str = ""


class GameSyncRequest(AccountSettings):
    days: int = Field(default=90, ge=1, le=3650)
    speeds: list[str] = Field(default_factory=lambda: ["blitz", "rapid", "classical"])
    rated_only: bool = True


class ImportResult(BaseModel):
    repertoire_id: str
    source_name: str
    games_found: int
    unique_lines: int
    cards_created: int
    duplicates_merged: int
    cards_admitted_today: int = 0
