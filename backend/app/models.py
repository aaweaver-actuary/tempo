from typing import Literal

from pydantic import BaseModel, Field


class Settings(BaseModel):
    initial_depth: int = Field(default=6, ge=2, le=20)
    timezone: str = "local"
    new_cards_per_day: int = Field(default=10, ge=0, le=100)


class ReviewRequest(BaseModel):
    rating: Literal["again", "hard", "good", "easy"]


class ImportResult(BaseModel):
    repertoire_id: str
    source_name: str
    games_found: int
    unique_lines: int
    cards_created: int
    duplicates_merged: int
