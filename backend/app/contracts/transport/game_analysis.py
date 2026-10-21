"""Games engine evidence contracts."""
from typing import Literal
from pydantic import BaseModel, Field
from .engine import EngineAttemptDiagnostics


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


class GameAnalysisPositionClaimRequest(BaseModel):
    preferred_game_id: str | None = Field(default=None, min_length=1)


class GameAnalysisLeaseRequest(BaseModel):
    """Request model for acquiring a game analysis lease."""
    diagnostics: EngineAttemptDiagnostics | None = None

    lease_id: str
