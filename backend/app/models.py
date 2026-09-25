from typing import Literal

from pydantic import BaseModel, Field


class Settings(BaseModel):
    """Model representing user settings for the application."""

    initial_depth: int = Field(default=6, ge=2, le=20)
    timezone: str = "local"
    tactics_new_per_day: int = Field(default=5, ge=0, le=100)
    defense_new_cards_per_day: int = Field(default=5, ge=0, le=100)
    include_defensive_cards_in_daily_stack: bool = True
    discovery_window_days: Literal[30, 90] = 90
    new_cards_per_day: int = Field(default=10, ge=0, le=100)
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
    source_gap_id: str | None = None


class AnalysisPastePreviewRequest(BaseModel):
    text: str
    starting_fen: str | None = None
    source_gap_id: str | None = None


class AnalysisPasteSelection(BaseModel):
    index: int = Field(ge=0)
    repertoire_id: str
    acknowledge_conflict: bool = False


class AnalysisPasteCommitRequest(AnalysisPastePreviewRequest):
    preview_token: str
    selections: list[AnalysisPasteSelection]


class IntegrityResolutionRequest(BaseModel):
    """Request for choosing the single response at an integrity issue."""

    signature: str
    selected_move_uci: str = Field(pattern=r"^[a-h][1-8][a-h][1-8][qrbn]?$", min_length=4, max_length=5)


class CoverageMaiaMove(BaseModel):
    move_uci: str = Field(pattern=r"^[a-h][1-8][a-h][1-8][qrbn]?$", min_length=4, max_length=5)
    probability: float = Field(ge=0, le=1)


class CoverageMaiaSubmission(BaseModel):
    node_id: str
    lease_id: str
    moves: list[CoverageMaiaMove]


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


class PrefixSplitRequest(BaseModel):
    """Accept a previously previewed one-decision prefix split."""

    expected_revision: int = Field(ge=1)


class PrefixSplitCard(BaseModel):
    """One card produced by a prefix split."""

    card_id: str
    starting_fen: str
    moves: list[str]
    tested_player_moves: int = Field(ge=1)


class PrefixSplitResponse(BaseModel):
    """Preview or result of splitting a long opening prefix."""

    source_card_id: str
    source_revision: int
    parent: PrefixSplitCard
    continuation: PrefixSplitCard
    applied: bool
    idempotent: bool = False
    shared_line_count: int = Field(default=1, ge=1)
    shared_repertoire_count: int = Field(default=1, ge=1)


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


class GameAnalysisCandidate(BaseModel):
    """One bounded engine candidate retained for tactical re-derivation."""

    # ``move_uci`` is accepted as a compatibility spelling for API clients that
    # use the persisted column name. New clients send the engine's ``uci`` key.
    uci: str | None = Field(default=None, pattern=r"^[a-h][1-8][a-h][1-8][qrbn]?$", min_length=4, max_length=5)
    move_uci: str | None = Field(default=None, pattern=r"^[a-h][1-8][a-h][1-8][qrbn]?$", min_length=4, max_length=5)
    cp: int | None = None
    score_cp: int | None = None
    mate: int | None = None
    score_mate: int | None = None
    score: str | None = Field(default=None, max_length=32)
    principal_variation: list[str] = Field(default_factory=list)
    pv: list[str] | None = None


class GameAnalysisEvaluation(BaseModel):
    """Engine evidence for one played move and its decision position."""

    ply: int = Field(ge=0)
    before_cp: int
    after_cp: int
    opponent_created_chance: bool = False
    depth: int | None = Field(default=None, ge=1, le=40)
    best_move_uci: str | None = Field(default=None, pattern=r"^[a-h][1-8][a-h][1-8][qrbn]?$", min_length=4, max_length=5)
    principal_variation: list[str] = Field(default_factory=list)
    mate_before: int | None = None
    mate_after: int | None = None
    mover_color: Literal["white", "black"] | None = None
    is_player_move: bool | None = None
    actual_move_uci: str | None = Field(default=None, pattern=r"^[a-h][1-8][a-h][1-8][qrbn]?$", min_length=4, max_length=5)
    position_fen: str | None = None
    candidate_lines: list[GameAnalysisCandidate] = Field(default_factory=list)
    candidates: list[GameAnalysisCandidate] | None = None


class GameAnalysisRequest(BaseModel):
    """Request model for analyzing a game."""

    evaluations: list[GameAnalysisEvaluation]
    depth: int = Field(default=13, ge=1, le=40)
    lease_id: str | None = None
    idempotency_key: str | None = None
    analysis_version: int = Field(default=1, ge=1)
    analysis_evidence_version: int = Field(default=2, ge=1, le=10)
    engine_version: str = "Stockfish 19 WASM"
    network_version: str = "nn-61e7af4bb97d.nnue"


class GameAnalysisFailureRequest(BaseModel):
    """Request model for reporting a game analysis failure."""

    lease_id: str
    error: str = Field(min_length=1, max_length=1000)


class GameAnalysisLeaseRequest(BaseModel):
    """Request model for acquiring a game analysis lease."""

    lease_id: str


class ThreatAnalysisSubmission(BaseModel):
    lease_id: str = Field(min_length=1)
    report: dict


class ThreatAnalysisFailureRequest(BaseModel):
    lease_id: str = Field(min_length=1)
    error: str = Field(min_length=1, max_length=1000)


class DefenseAttemptRequest(BaseModel):
    attempt_id: str = Field(min_length=1, max_length=100)
    exercise_revision: int = Field(ge=1)
    queue_entry_id: int = Field(ge=1)
    move_uci: str = Field(min_length=4, max_length=5)
    recognition_attempt_id: str | None = Field(default=None, min_length=1, max_length=100)


class DefenseRecognitionRequest(BaseModel):
    attempt_id: str = Field(min_length=1, max_length=100)
    exercise_revision: int = Field(ge=1)
    rubric_version: int | None = Field(default=None, ge=1)
    queue_entry_id: int = Field(ge=1)
    no_concrete_threat: bool = False
    dangerous_piece_square: str | None = Field(default=None, pattern=r"^[a-h][1-8]$")
    destination_square: str | None = Field(default=None, pattern=r"^[a-h][1-8]$")
    king_square: str | None = Field(default=None, pattern=r"^[a-h][1-8]$")
    major_square: str | None = Field(default=None, pattern=r"^[a-h][1-8]$")
    consequence: Literal["checking_fork", "other", "none"]
    hinted: bool = False


class DiscoveryAcceptanceRequest(BaseModel):
    selected_move_uci: str = Field(pattern=r"^[a-h][1-8][a-h][1-8][qrbn]?$")
    evidence_fingerprint: str = Field(min_length=1)


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


class GameFindingCurationRequest(BaseModel):
    """Request for a non-training decision in the tactical curation queue."""

    action: Literal["skip", "ignore"]


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


class GameTimelineEvent(BaseModel):
    """One public event on a game's repertoire/analysis timeline."""

    ply: int = Field(ge=0)
    kind: str


class GamePublicRecord(BaseModel):
    """Allowlisted game transport record; SQLite bookkeeping stays private."""

    id: str
    provider: GameProvider
    username: str
    played_at: str
    speed: str
    rated: Literal[0, 1]
    color: Literal["white", "black"]
    result: str
    start_fen: str
    moves: list[str]
    game_url: str | None = None
    opening_name: str | None = None
    analysis_state: Literal["pending", "analyzing", "ready", "complete", "failed"]
    analysis_version: int = Field(ge=0)
    major_mistake_ply: int | None = None
    missed_punishment_ply: int | None = None
    repertoire_id: str | None = None
    classification: str | None = None
    divergence_ply: int | None = None
    divergence_fen: str | None = None
    expected: list[str] = Field(default_factory=list)
    actual_uci: str | None = None
    deviation_card_id: str | None = None
    matched_player_decisions: int | None = None
    repertoire_opportunities: int | None = None
    deepest_covered_ply: int | None = None
    first_opponent_gap_ply: int | None = None
    out_of_book_ply: int | None = None
    timeline: list[GameTimelineEvent] = Field(default_factory=list)
    adherence: float | None = None


class GamesSummaryResponse(BaseModel):
    """Typed response for the Games library."""

    total: int = Field(ge=0)
    games: list["GameSummaryRecord"]
    next_cursor: str | None = None
    aggregates: dict[str, int] = Field(default_factory=dict)


class GameSummaryRecord(BaseModel):
    """Lightweight library row. Moves and evidence are fetched by game ID."""

    id: str
    provider: GameProvider
    played_at: str
    speed: str
    color: Literal["white", "black"]
    result: str
    opening_name: str | None = None
    analysis_state: Literal["pending", "analyzing", "ready", "complete", "failed"]
    major_mistake_ply: int | None = None
    missed_punishment_ply: int | None = None
    repertoire_id: str | None = None
    classification: str | None = None
    divergence_ply: int | None = None
    matched_player_decisions: int | None = None
    repertoire_opportunities: int | None = None
    adherence: float | None = None


class ImportResult(BaseModel):
    """Model representing the result of importing games into the repertoire."""

    repertoire_id: str
    source_name: str
    games_found: int
    unique_lines: int
    cards_created: int
    duplicates_merged: int
    cards_admitted_today: int = 0
    integrity: dict = Field(default_factory=dict)
    decision_cards_created: int = 0
    shared_decisions_reused: int = 0
    prefix_cards_created: int = 0
    shared_prefixes_reused: int = 0
    descendant_decision_cards_created: int = 0
    graph_state: Literal["refreshing", "ready", "failed"] = "refreshing"


class TacticActivationRequest(BaseModel):
    """Request model for activating or deactivating tactic packs."""

    pack_ids: list[str] = Field(min_length=1, max_length=692)
    active: bool


class GuidedReviewAttemptRequest(BaseModel):
    """A legal correction attempted from the hidden-answer position."""

    move_uci: str = Field(pattern=r"^[a-h][1-8][a-h][1-8][qrbn]?$", min_length=4, max_length=5)
