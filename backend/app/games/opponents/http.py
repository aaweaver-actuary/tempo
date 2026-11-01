"""HTTP adapter for the published next-opponent profile."""
from collections.abc import Callable

from fastapi import APIRouter

from app.contracts.next_opponent import NextOpponentProfileResponse, ProfileSpeed


def create_next_opponent_router(
    read_profile: Callable[[ProfileSpeed], NextOpponentProfileResponse],
) -> APIRouter:
    """Build a router around the supplied read operation without starting work."""
    router = APIRouter()

    @router.get("/api/games/next-opponent-profile", response_model=NextOpponentProfileResponse)
    def next_opponent_profile(speed: ProfileSpeed = "auto"):
        return read_profile(speed)

    return router
