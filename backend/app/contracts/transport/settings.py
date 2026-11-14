"""Product settings contract."""

from pydantic import BaseModel, Field


class Settings(BaseModel):
    """Model representing user settings for the application."""

    initial_depth: int = Field(default=6, ge=2, le=20)
    timezone: str = "local"
    tactics_new_per_day: int = Field(default=5, ge=0, le=100)
    study_only_mode: bool = False
    new_cards_per_day: int = Field(default=10, ge=0, le=100)
    study_new_per_day: int = Field(default=2, ge=0, le=100)
    light_first_interval_days: int = Field(default=7, ge=1, le=90)
