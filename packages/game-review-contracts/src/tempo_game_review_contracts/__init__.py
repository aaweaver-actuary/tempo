"""Shared values and ports; no providers, storage, component or action imports."""
from datetime import datetime
from typing import Literal, Protocol
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Value(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class CanonicalGame(Value):
    schema_version: Literal[1] = 1
    workspace_id: str = Field(min_length=1, max_length=200)
    game_id: str = Field(min_length=1, max_length=200)
    game_revision: int = Field(ge=1)
    source_id: str = Field(min_length=1, max_length=200)
    source_revision: int = Field(ge=1)
    source_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    starting_fen: str = Field(min_length=1, max_length=200)
    moves: list[str] = Field(max_length=1000)
    learner_color: Literal["white", "black"]
    played_at: str | None

    @field_validator("played_at")
    @classmethod
    def known_time_requires_zone(cls, value):
        if value is not None and datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("known game time requires a timezone")
        return value


class ReentryAuthority(Value):
    ply: int = Field(ge=0, le=1000)
    authority_id: str = Field(min_length=1, max_length=200)


class AuthoredRoute(Value):
    repertoire_id: str = Field(min_length=1, max_length=200)
    route_id: str = Field(min_length=1, max_length=200)
    policy_id: str = Field(min_length=1, max_length=200)
    learner_color: Literal["white", "black"]
    starting_fen: str = Field(min_length=1, max_length=200)
    moves: list[str] = Field(max_length=1000)
    reentries: list[ReentryAuthority] = Field(default_factory=list, max_length=1000)


class RepertoireSnapshot(Value):
    schema_version: Literal[1] = 1
    workspace_id: str = Field(min_length=1, max_length=200)
    snapshot_id: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=1)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_revision: str = Field(min_length=1, max_length=200)
    captured_at: str
    basis: Literal["current_repertoire"] = "current_repertoire"
    scope_id: str = Field(min_length=1, max_length=200)
    scope_repertoire_ids: list[str] = Field(min_length=1, max_length=10000)
    complete: bool
    available: bool = True
    unavailable_reason: str | None = None
    routes: list[AuthoredRoute] = Field(max_length=10000)

    @field_validator("captured_at")
    @classmethod
    def capture_time_requires_zone(cls, value):
        if datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("snapshot capture time requires a timezone")
        return value


class RouteContext(Value):
    repertoire_id: str
    route_id: str
    policy_id: str
    route_ply: int
    reentry_authority_id: str | None = None


class PositionFact(Value):
    ply: int = Field(ge=0)
    fen: str
    side: Literal["learner", "opponent"]
    actual_move: str
    expected_moves: list[str]
    status: Literal["matched", "different", "unknown"]
    reason: Literal["authored_move", "move_not_authored", "route_ended", "no_applicable_route", "ambiguous", "unavailable_evidence", "after_departure"]
    route_contexts: list[RouteContext]


class ComparisonReport(Value):
    schema_version: Literal[1] = 1
    report_id: str
    workspace_id: str
    game_id: str
    game_revision: int
    source_digest: str
    source_id: str
    source_revision: int = Field(ge=1)
    snapshot_id: str
    snapshot_revision: int
    snapshot_digest: str
    snapshot_source_revision: str
    snapshot_captured_at: str
    scope_id: str
    scope_repertoire_ids: list[str]
    snapshot_complete: bool
    snapshot_available: bool
    basis: Literal["supplied_current_repertoire"] = "supplied_current_repertoire"
    matched_prefix_plies: int = Field(ge=0)
    positions: list[PositionFact]
    first_difference: PositionFact | None
    limitations: list[str] = Field(default_factory=list)


class GameInput(Protocol):
    def read(self) -> bytes: ...


class GameIngester(Protocol):
    def ingest(self, source: GameInput, *, workspace_id: str, game_id: str,
               game_revision: int, source_id: str, source_revision: int,
               player: str, played_at: str | None = None) -> CanonicalGame: ...


class RepertoireComparer(Protocol):
    def compare(self, game: CanonicalGame, snapshot: RepertoireSnapshot) -> ComparisonReport: ...


class Unavailable(Value):
    status: Literal["not_implemented"] = "not_implemented"
    component: Literal["classification", "actions"]
    reason: str


class ClassificationPort(Protocol):
    def classify(self, report: ComparisonReport) -> Unavailable: ...


class ActionRequest(Value):
    """Report reference only; no mutation authority or executable payload."""
    workspace_id: str = Field(min_length=1, max_length=200)
    report_id: str = Field(min_length=1, max_length=200)


class ActionPort(Protocol):
    def execute(self, request: ActionRequest) -> Unavailable: ...


from .replay import replay_line

__all__ = ['CanonicalGame', 'ReentryAuthority', 'AuthoredRoute', 'RepertoireSnapshot', 'RouteContext', 'PositionFact', 'ComparisonReport', 'GameInput', 'GameIngester', 'RepertoireComparer', 'Unavailable', 'ClassificationPort', 'ActionRequest', 'ActionPort', 'replay_line']
