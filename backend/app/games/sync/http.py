"""Games sync HTTP transport, composed with application-owned capabilities."""

from collections.abc import Callable

from fastapi import APIRouter, Header, HTTPException, Response

from .interfaces import GameSyncEnqueueResponse, GameSyncRequest, GameSyncStatusResponse


def create_game_sync_router(
    enqueue: Callable[
        [GameSyncRequest, str | None], GameSyncEnqueueResponse | Response
    ],
    read_status: Callable[[], GameSyncStatusResponse],
) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/api/games/sync", response_model=GameSyncEnqueueResponse, status_code=202
    )
    def sync(
        request: GameSyncRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        users = (request.lichess_username.strip(), request.chesscom_username.strip())
        if not any(users):
            raise HTTPException(422, "Set a Lichess or Chess.com username in Settings")
        normalized = request.model_copy(
            update={"lichess_username": users[0], "chesscom_username": users[1]}
        )
        return enqueue(normalized, idempotency_key)

    @router.get("/api/games/sync/status", response_model=GameSyncStatusResponse)
    def sync_status():
        return read_status()

    return router
