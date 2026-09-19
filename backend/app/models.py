from typing import Literal

from pydantic import BaseModel, Field


class Settings(BaseModel):
    """Model representing user settings for the application."""

    initial_depth: int = Field(default=6, ge=2, le=20)
    timezone: str = "local"
    tactics_new_per_day: int = Field(default=5, ge=0, le=100)
    new_cards_per_day: int = Field(default=10, ge=0, le=100)
    lichess_username: str = ""
    chesscom_username: str = ""
    auto_sync_minutes: int = Field(default=3, ge=2, le=60)
    engine_line_window_cp: int = Field(default=30, ge=0, le=300)
    major_mistake_cp: int = Field(default=100, ge=25, le=1000)
    light_first_interval_days: int = Field(default=7, ge=1, le=90)
    draw_hold_user_moves: int = Field(default=20, ge=5, le=100)


class ReviewRequest(BaseModel):
    """Request model for reviewing a card."""

    outcome: Literal["correct", "again"]
    guided: bool = False
    queue_entry_id: int | None = None


class BranchRequest(BaseModel):
    """Request model for creating a new analysis branch."""

    repertoire_id: str
    starting_fen: str
    moves: list[str]
    trained_color: Literal["white", "black"]
    name: str = "Analysis branch"
    allow_conflict: bool = False


class RemoveBranchRequest(BaseModel):
    """Request model for removing an analysis branch."""

    repertoire_id: str
    starting_fen: str
    moves: list[str]


class CardRevisionRequest(BaseModel):
    """Request model for revising a card."""

    starting_fen: str
    moves: list[str]
    history_mode: Literal["preserve", "reset"]
    title: str = "Corrected card"
    source_fen: str | None = None


class RepertoireRenameRequest(BaseModel):
    """Request model for renaming a repertoire."""

    name: str = Field(min_length=1, max_length=80)


class AnnotationArrow(BaseModel):
    """Model representing an annotation arrow on a chessboard."""

    from_square: str = Field(alias="from", pattern=r"^[a-h][1-8]$")
    to: str = Field(pattern=r"^[a-h][1-8]$")
    color: Literal["green", "red", "blue", "yellow"] = "green"


class AnnotationSquare(BaseModel):
    """Model representing an annotation square on a chessboard."""

    square: str = Field(pattern=r"^[a-h][1-8]$")
    color: Literal["green", "red", "blue", "yellow"] = "green"


class PositionAnnotationRequest(BaseModel):
    """Request model for annotating a position on the chessboard."""

    fen: str
    comment: str = Field(default="", max_length=4000)
    arrows: list[AnnotationArrow] = Field(default_factory=list)
    squares: list[AnnotationSquare] = Field(default_factory=list)


class TeachingStateRequest(BaseModel):
    """Request model for the teaching state of a card."""

    revision: int = Field(default=1, ge=1)
    ply: int = Field(ge=0)


class TacticAttemptRequest(BaseModel):
    """Request model for attempting a tactic puzzle."""

    attempt_id: str | None = None
    puzzle_id: str
    deck_id: str
    correct: bool
    clean: bool = True
    source_fen: str
    moves: list[str]
    rating: int = 1500


class EndgameTemplateRequest(BaseModel):
    """Request model for creating an endgame template."""

    name: str = Field(min_length=1, max_length=80)
    white_material: str
    black_material: str
    trained_color: Literal["white", "black"]
    goal_mix: Literal["win", "draw", "both"] = "both"


class EndgameProbeRequest(BaseModel):
    """Request model for probing an endgame position."""

    fen: str


class GameAnalysisRequest(BaseModel):
    """Request model for analyzing a game."""

    evaluations: list[dict]
    depth: int = Field(default=13, ge=1, le=40)
    lease_id: str | None = None
    idempotency_key: str | None = None
    analysis_version: int = Field(default=1, ge=1)
    engine_version: str = "Stockfish 19 WASM"
    network_version: str = "nn-1c0000000000.nnue"


class GameAnalysisFailureRequest(BaseModel):
    """Request model for reporting a game analysis failure."""

    lease_id: str
    error: str = Field(min_length=1, max_length=1000)


class GameAnalysisLeaseRequest(BaseModel):
    """Request model for acquiring a game analysis lease."""

    lease_id: str


class GameFindingDecisionRequest(BaseModel):
    """Request model for making a decision on a game finding."""

    decision: Literal["accepted", "ignored"]


class GameExclusionRequest(BaseModel):
    """Request model for excluding a game."""

    excluded: bool


class GameFindingCardRequest(BaseModel):
    """Request model for creating a card from a game finding."""

    save: bool = False
    starting_fen: str | None = None
    moves: list[str] | None = None
    trained_color: Literal["white", "black"] | None = None


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


class GameSyncJob(BaseModel):
    """Model representing a game sync job."""

    id: str
    status: GameSyncJobState
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str
    error: str | None = None
    result: dict | None = None


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


class ImportResult(BaseModel):
    """Model representing the result of importing games into the repertoire."""

    repertoire_id: str
    source_name: str
    games_found: int
    unique_lines: int
    cards_created: int
    duplicates_merged: int
    cards_admitted_today: int = 0


class TacticActivationRequest(BaseModel):
    """Request model for activating or deactivating tactic packs."""

    pack_ids: list[str] = Field(min_length=1, max_length=692)
    active: bool
