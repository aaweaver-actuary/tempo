"""Strict service-local input schema 1; these are not shared contract additions."""

from __future__ import annotations

import re
from typing import Annotated, Literal

import chess
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from .identity import canonical_position, legal_universe_digest

Identifier = Annotated[str, Field(min_length=1, max_length=200)]
Revision = Annotated[int, Field(ge=1, le=2**53 - 1)]
UciMove = Annotated[str, Field(pattern=r"^[a-h][1-8][a-h][1-8][qrbn]?$")]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]

LICHESS_RATINGS = (0, 1000, 1200, 1400, 1600, 1800, 2000, 2200, 2500)
LICHESS_SPEEDS = ("ultraBullet", "bullet", "blitz", "rapid", "classical", "correspondence")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, validate_default=True, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def integer_schema_version(cls, value):
        if isinstance(value, dict) and "schema_version" in value and type(value["schema_version"]) is not int:
            raise ValueError("invalid_schema_version")
        return value


class Snapshot(StrictModel):
    schema_version: Literal[1]
    workspace_id: Identifier
    snapshot_id: Identifier
    source_id: Identifier
    source_revision: Revision
    publication_id: Identifier
    publication_generation: Revision
    fen: str = Field(min_length=1, max_length=120)
    position_key: Identifier
    legal_universe_version: Identifier
    legal_universe_digest: Digest
    complete: bool
    legal_moves: list[UciMove] = Field(max_length=256)
    policy_context: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def verified_legal_universe(self):
        position_key, canonical_fen = canonical_position(self.fen)
        if self.position_key != position_key:
            raise ValueError("position_key_mismatch")
        if not self.complete:
            raise ValueError("incomplete_legal_universe")
        legal_moves = [move.uci() for move in chess.Board(canonical_fen).legal_moves]
        if len(set(self.legal_moves)) != len(self.legal_moves) or set(self.legal_moves) != set(legal_moves):
            raise ValueError("legal_universe_mismatch")
        if self.legal_universe_digest != legal_universe_digest(position_key, self.legal_moves, self.legal_universe_version):
            raise ValueError("legal_universe_digest_mismatch")
        return self


class LichessSource(StrictModel):
    kind: Literal["lichess"]
    ratings: list[int] = Field(default_factory=lambda: list(LICHESS_RATINGS), min_length=1, max_length=9)
    speeds: list[str] = Field(default_factory=lambda: list(LICHESS_SPEEDS), min_length=1, max_length=6)
    since: str = "1952-01"
    until: str = "3000-12"

    @model_validator(mode="after")
    def supported_cohort(self):
        if len(set(self.ratings)) != len(self.ratings) or any(rating not in LICHESS_RATINGS for rating in self.ratings):
            raise ValueError("unsupported_rating_cohort")
        if len(set(self.speeds)) != len(self.speeds) or any(speed not in LICHESS_SPEEDS for speed in self.speeds):
            raise ValueError("unsupported_speed_cohort")
        if any(not re.fullmatch(r"[0-9]{4}-(?:0[1-9]|1[0-2])", value) or not "1952-01" <= value <= "3000-12" for value in (self.since, self.until)) or self.since > self.until:
            raise ValueError("unsupported_date_range")
        object.__setattr__(self, "ratings", sorted(self.ratings))
        object.__setattr__(self, "speeds", [speed for speed in LICHESS_SPEEDS if speed in self.speeds])
        return self


class MastersSource(StrictModel):
    kind: Literal["masters"]
    since: int = Field(default=1952, ge=1952, le=3000)
    until: int = Field(default=3000, ge=1952, le=3000)

    @model_validator(mode="after")
    def supported_date_range(self):
        if self.since > self.until:
            raise ValueError("unsupported_date_range")
        return self


class MaiaSource(StrictModel):
    kind: Literal["maia"]
    rating: int = Field(ge=0, le=4000)
    model_id: Identifier
    model_version: Identifier
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


Source = Annotated[LichessSource | MastersSource | MaiaSource, Field(discriminator="kind")]


class RequestInput(StrictModel):
    schema_version: Literal[1]
    request_id: Identifier
    request_revision: Revision
    expected_revision: Revision | None = None
    snapshot: Snapshot
    sources: list[Source] = Field(min_length=1, max_length=3)
    max_age_seconds: int = Field(default=86400, ge=0, le=31536000)

    @model_validator(mode="after")
    def distinct_sources(self):
        kinds = [source.kind for source in self.sources]
        if len(set(kinds)) != len(kinds):
            raise ValueError("duplicate_source")
        return self
