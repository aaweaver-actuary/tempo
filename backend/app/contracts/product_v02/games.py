"""Provider acquisition and comparison facts. No provider or Study implementation."""
from typing import Literal, Protocol

from pydantic import Field, model_validator
from tempo_game_review_contracts import CanonicalGame  # Existing canonical identity owner.

from . import Digest, Identifier, Revision, UciMove, UtcTimestamp, Value

TimePrecision = Literal["unknown", "day", "hour", "minute", "second", "millisecond"]


class GameWindow(Value):
    start_at: UtcTimestamp
    end_at: UtcTimestamp
    precision: TimePrecision

    @model_validator(mode="after")
    def ordered_window(self):
        from datetime import datetime
        if datetime.fromisoformat(self.start_at.replace("Z", "+00:00")) >= datetime.fromisoformat(self.end_at.replace("Z", "+00:00")):
            raise ValueError("window is half-open [start_at, end_at) and must be nonempty")
        return self


class GameRecord(Value):
    source_game_id: Identifier
    source_revision: Revision
    source_digest: Digest
    pgn: str = Field(min_length=1)
    played_at: UtcTimestamp | None
    time_precision: TimePrecision

    @model_validator(mode="after")
    def explicit_unknown_time(self):
        if (self.played_at is None) != (self.time_precision == "unknown"):
            raise ValueError("unknown source time must remain null with unknown precision")
        return self


class GameRejection(Value):
    source_game_id: Identifier | None
    source_revision: Revision | None
    reason: Identifier


class GamePage(Value):
    interface_version: Literal["0.2.0"] = "0.2.0"
    provider: Literal["lichess", "chesscom"]
    account: Identifier
    window: GameWindow
    records: tuple[GameRecord, ...]
    rejections: tuple[GameRejection, ...]
    continuation: str | None
    complete: bool
    saturated: bool
    filters: tuple[tuple[str, str], ...] = ()

    @model_validator(mode="after")
    def honest_completeness(self):
        if self.complete and (self.saturated or self.continuation is not None):
            raise ValueError("saturated or continuing page cannot be complete")
        ids = [item.source_game_id for item in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("a page cannot contain conflicting revisions of one source game")
        return self


class GameAcquisitionPort(Protocol):
    def fetch_page(self, *, account: str, window: GameWindow, continuation: str | None = None) -> GamePage: ...


class GameSummary(Value):
    """Read-only screen projection; canonical normalization keeps its existing owner."""
    provider: Literal["lichess", "chesscom"]
    game_id: Identifier
    source_revision: Revision
    played_at: UtcTimestamp | None
    time_precision: TimePrecision
    player_color: Literal["white", "black"]

    @model_validator(mode="after")
    def explicit_unknown_time(self):
        if (self.played_at is None) != (self.time_precision == "unknown"):
            raise ValueError("unknown source time remains null with unknown precision")
        return self


class ProviderSyncStatus(Value):
    provider: Literal["lichess", "chesscom"]
    status: Literal["idle", "pending", "complete", "failed"]
    operation_id: Identifier | None
    error: Identifier | None

    @model_validator(mode="after")
    def honest_operation_status(self):
        if (self.status == "failed") != (self.error is not None):
            raise ValueError("failed provider status requires an explicit error")
        if self.status != "idle" and self.operation_id is None:
            raise ValueError("non-idle status requires operation identity")
        return self


class RecentGames(Value):
    games: tuple[GameSummary, ...]
    sync: tuple[ProviderSyncStatus, ...]


class GameRefreshStatus(Value):
    provider: Literal["lichess", "chesscom"]
    operation_id: Identifier
    job_id: Identifier
    window: GameWindow
    status: Literal["queued", "running", "retryable", "failed", "quarantined", "complete"]
    pages_committed: int = Field(ge=0)
    next_attempt_at: float = Field(ge=0, allow_inf_nan=False)
    error: str | None


FindingKind = Literal["confirmed_miss", "opponent_gap", "missing_own_continuation", "known_unintroduced", "known_unavailable", "conflict", "ambiguous", "coverage_end"]


class Finding(Value):
    interface_version: Literal["0.2.0"] = "0.2.0"
    kind: FindingKind
    provider: Literal["lichess", "chesscom"]
    game_id: Identifier
    source_revision: Revision
    source_digest: Digest
    ply: int = Field(ge=1, le=1000)
    position_key: Identifier
    expected_uci: tuple[UciMove, ...]
    played_uci: UciMove
    repertoire_id: Identifier
    repertoire_revision: Revision
    decision_id: Identifier | None
    presentation_id: Identifier | None
    presentation_revision: Revision | None
    introduced_before_game: bool | None
    membership: Literal["present", "absent", "conflict", "unknown"]
    lapse_eligible: bool
    diagnostic: Identifier | None
    reentry_authority_id: Identifier | None = None

    @model_validator(mode="after")
    def eligibility_requires_proof(self):
        if self.kind == "confirmed_miss":
            if not (self.decision_id and self.presentation_id and self.presentation_revision and self.introduced_before_game is True and self.membership == "present" and self.lapse_eligible):
                raise ValueError("confirmed miss requires an introduced, mapped current presentation")
            if len(self.expected_uci) != 1 or self.played_uci in self.expected_uci:
                raise ValueError("confirmed miss requires one selected own response different from played")
        elif self.lapse_eligible or self.diagnostic is None:
            raise ValueError("non-miss finding requires a diagnostic and cannot authorize a lapse")
        if (self.presentation_id is None) != (self.presentation_revision is None):
            raise ValueError("presentation identity and revision must be supplied together")
        return self


__all__ = ["CanonicalGame", "GameWindow", "GameRecord", "GameRejection", "GamePage", "GameAcquisitionPort", "Finding", "FindingKind", "GameSummary", "ProviderSyncStatus", "RecentGames", "GameRefreshStatus"]
