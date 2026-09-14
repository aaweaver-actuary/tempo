from typing import Literal

from pydantic import BaseModel, Field


class Settings(BaseModel):
    initial_depth: int = Field(default=6, ge=2, le=20)
    timezone: str = "local"
    new_cards_per_day: int = Field(default=10, ge=0, le=100)
    lichess_username: str = ""
    chesscom_username: str = ""


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
