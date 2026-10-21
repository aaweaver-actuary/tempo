"""Product settings contract."""
from typing import Literal
from pydantic import BaseModel, Field


class Settings(BaseModel):
    """Model representing user settings for the application."""

    initial_depth: int = Field(default=6, ge=2, le=20)
    timezone: str = "local"
    tactics_new_per_day: int = Field(default=5, ge=0, le=100)
    defense_new_cards_per_day: int = Field(default=5, ge=0, le=100)
    include_defensive_cards_in_daily_stack: bool = True
    defensive_analysis_enabled: bool = False
    study_only_mode: bool = False
    discovery_window_days: Literal[30, 90] = 90
    new_cards_per_day: int = Field(default=10, ge=0, le=100)
    study_new_per_day: int = Field(default=2, ge=0, le=100)
    lichess_username: str = ""
    chesscom_username: str = ""
    auto_sync_minutes: int = Field(default=3, ge=2, le=60)
    engine_line_window_cp: int = Field(default=30, ge=0, le=300)
    major_mistake_cp: int = Field(default=100, ge=25, le=1000)
    light_first_interval_days: int = Field(default=7, ge=1, le=90)
    draw_hold_user_moves: int = Field(default=20, ge=5, le=100)
    coverage_reply_denominator: int = Field(default=100, ge=2, le=10000)
    coverage_cumulative_target: int = Field(default=95, ge=50, le=100)
    coverage_horizon_fullmoves: int = Field(default=15, ge=4, le=40)
    coverage_path_floor: float = Field(default=0.0005, ge=0, le=0.1)
    coverage_maia_elo: int = Field(default=1500, ge=1100, le=1900)
