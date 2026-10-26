"""Unregistered Game Review HTTP adapter. Reads never initiate optional work.

The injected owner supplies stored projections and durable commands. This file
does not create a coordinator, access a table or call a Study implementation.
"""
import json
from typing import Literal, Protocol

from fastapi import APIRouter
from pydantic import field_validator

from app.contracts.product_v02 import Identifier, Value
from app.contracts.product_v02.games import Finding, ProviderSyncStatus, RecentGames
from app.contracts.product_v02.learning import LearningReceipt
from app.contracts.product_v02.presentation import GameDetail

Provider = Literal["lichess", "chesscom"]


class SyncRequest(Value):
    provider: Provider
    operation_id: Identifier


class RetryFeedbackRequest(Value):
    finding: Finding
    operation_id: Identifier

    @field_validator("finding", mode="before")
    @classmethod
    def parse_wire_finding(cls, value):
        # Strict immutable contracts accept JSON arrays through their JSON parser.
        # FastAPI has already decoded the outer request to Python by this point.
        return Finding.model_validate_json(json.dumps(value)) if isinstance(value, dict) else value


class GameReviewOperations(Protocol):
    """Small HTTP consumer capability; implementations retain owner authority.

    sync only durably enqueues a configured job. retry_feedback must resolve the
    stored current finding, reject stale/forged inputs, and reuse its logical key.
    Both require durable operation replay handling; HTTP success is not admission.
    Reads only project stored facts, including actual Study receipts.
    """
    def recent_games(self) -> RecentGames: ...
    def game_detail(self, *, provider: Provider, game_id: str,
                    repertoire_id: str | None) -> GameDetail: ...
    def sync(self, *, provider: Provider, operation_id: str) -> ProviderSyncStatus: ...
    def retry_feedback(self, *, finding: Finding, operation_id: str) -> LearningReceipt: ...


def create_game_review_router(operations: GameReviewOperations) -> APIRouter:
    """Explicitly mounted later by composition; no default/global owner."""
    router = APIRouter(prefix="/api/product/game-review", tags=["game-review"])

    @router.get("/games", response_model=RecentGames)
    def recent_games():
        return operations.recent_games()

    @router.get("/games/{provider}/{game_id}", response_model=GameDetail)
    def game_detail(provider: Provider, game_id: Identifier,
                    repertoire_id: Identifier | None = None):
        return operations.game_detail(provider=provider, game_id=game_id,
                                      repertoire_id=repertoire_id)

    @router.post("/sync", response_model=ProviderSyncStatus)
    def sync(request: SyncRequest):
        return operations.sync(provider=request.provider, operation_id=request.operation_id)

    @router.post("/feedback/retry", response_model=LearningReceipt)
    def retry_feedback(request: RetryFeedbackRequest):
        return operations.retry_feedback(finding=request.finding,
                                         operation_id=request.operation_id)

    return router
