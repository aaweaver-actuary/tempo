"""Games provider/sync contracts."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class AccountSettings(BaseModel):
    """Model representing account settings for chess platforms."""

    lichess_username: str = ""
    chesscom_username: str = ""


class GameSyncRequest(AccountSettings):
    """Request model for syncing games from chess platforms."""

    days: int = Field(default=90, ge=1, le=3650)
    speeds: list[str] = Field(default_factory=lambda: ["blitz", "rapid", "classical"])
    rated_only: bool = True
    repair: bool = False


GameProvider = Literal["lichess", "chess.com"]


ProviderSyncState = Literal["idle", "syncing", "error"]


GameSyncJobState = Literal[
    "queued", "running", "paused", "retrying", "complete", "failed"
]


class ProviderSyncResult(BaseModel):
    """Model representing the result of syncing a provider."""

    provider: GameProvider
    username: str
    status: Literal["idle", "error"]
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    duplicates: int = 0
    filtered: int = 0
    rejected: int = 0
    failed: int = 0
    error: str | None = None
    retry_after: str | None = None


class ProviderSyncStatus(BaseModel):
    """Model representing the current sync status of a provider."""

    provider: GameProvider
    username: str
    status: ProviderSyncState
    cursor: str | None = None
    last_started_at: str | None = None
    last_success_at: str | None = None
    last_error: str | None = None
    retry_after: str | None = None
    last_result: ProviderSyncResult | None = None


class CompletedProviderSyncResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: GameProvider
    username: str
    status: Literal["idle", "error"]
    fetched: int
    inserted: int
    updated: int
    duplicates: int
    filtered: int
    rejected: int
    failed: int
    error: str | None
    retry_after: str | None


class CompletedGameSyncResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    imported: int
    synced_at: str
    cached: bool
    incremental: bool
    providers: dict[GameProvider, CompletedProviderSyncResult]


class GameSyncJob(BaseModel):
    """Model representing a game sync job."""

    id: str
    status: GameSyncJobState
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str
    error: str | None = None
    result: CompletedGameSyncResult | None = None

    @model_validator(mode="after")
    def completed_result_matches_status(self):
        if (self.status == "complete") != (self.result is not None):
            raise ValueError("Only complete game sync jobs may expose a completed result")
        return self


class GameSyncEnqueueResponse(BaseModel):
    """Response model for enqueuing a game sync job."""

    imported: int = 0
    job_id: str
    status: GameSyncJobState
    providers: dict[GameProvider, ProviderSyncResult] = Field(default_factory=dict)


class ActiveGameFilters(BaseModel):
    """Model representing the active filters for game synchronization."""

    days: int
    speeds: list[str]
    rated_only: bool


class GameSyncStatusResponse(BaseModel):
    """Response model representing the current status of game synchronization."""

    providers: list[ProviderSyncStatus]
    active_filters: ActiveGameFilters
    active_job: GameSyncJob | None = None
