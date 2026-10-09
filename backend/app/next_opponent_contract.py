"""Immutable issue #107 contracts; freshness is separate from model identity."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ProfileSpeed = Literal["auto", "blitz", "rapid", "classical"]


class ImmutableProfileModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SpeedWeight(ImmutableProfileModel):
    speed: str
    weight: float = Field(ge=0, le=1)


class RatingWeight(ImmutableProfileModel):
    rating: int
    weight: float = Field(ge=0, le=1)


class OpponentCohort(ImmutableProfileModel):
    speed: Literal["blitz", "rapid", "classical"]
    player_rating: int | None
    rating_observed_at: str | None
    latest_game_at: str | None
    game_count: int
    rating_pair_count: int
    effective_sample_size: float
    prior_weight: float
    opponent_ratings: tuple[RatingWeight, ...]
    quality_flags: tuple[str, ...]


class NextOpponentProfile(ImmutableProfileModel):
    version: str
    method_version: str
    source_account: str
    evidence_digest: str
    evidence_watermark: str | None
    game_count: int
    speed_mixture: tuple[SpeedWeight, ...]
    unsupported_speed_mass: float
    cohorts: tuple[OpponentCohort, ...]
    quality_flags: tuple[str, ...]


class NextOpponentProfileResponse(ImmutableProfileModel):
    availability: Literal["available", "unknown", "pending", "unsupported"]
    refresh_status: Literal["idle", "pending", "failed", "sync_error"]
    stale: bool
    stale_reasons: tuple[str, ...] = ()
    stale_cohorts: tuple[str, ...] = ()
    source_account: str | None = None
    last_successful_sync_at: str | None = None
    published_at: str | None = None
    requested_speed: ProfileSpeed = "auto"
    effective_speed_mixture: tuple[SpeedWeight, ...] = ()
    profile: NextOpponentProfile | None = None
    detail: str | None = None
