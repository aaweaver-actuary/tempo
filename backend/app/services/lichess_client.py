"""Lichess game export client with explicit validation and accounting."""

from __future__ import annotations

import io

import chess.pgn
import httpx

from .game_normalizer import RejectedGame, normalize_provider_pgn
from .game_record import GameRecord


class ProviderRequestError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502, retry_after: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


async def fetch_lichess_games(
    username: str,
    since_ms: int,
    speeds: list[str],
    rated_only: bool,
    client: httpx.AsyncClient,
) -> tuple[list[GameRecord], dict[str, int]]:
    response = await client.get(
        f"https://lichess.org/api/games/user/{username}",
        params={
            "since": since_ms,
            "moves": "true",
            "opening": "true",
            "perfType": ",".join(speeds),
            "rated": str(rated_only).lower(),
            "sort": "dateAsc",
        },
        headers={"Accept": "application/x-chess-pgn"},
    )
    if response.status_code == 404:
        raise ProviderRequestError("Lichess username not found", 404)
    if response.status_code == 429:
        raise ProviderRequestError(
            "Lichess rate limit reached", 429, response.headers.get("Retry-After")
        )
    if not response.is_success:
        raise ProviderRequestError(f"Lichess sync failed ({response.status_code})", response.status_code)
    content_type = response.headers.get("content-type", "").lower()
    if "html" in content_type:
        raise ProviderRequestError("Lichess returned HTML instead of games")

    records: list[GameRecord] = []
    rejected = 0
    pgn_stream = io.StringIO(response.text)
    while game := chess.pgn.read_game(pgn_stream):
        exported = game.accept(chess.pgn.StringExporter(headers=True, variations=False, comments=False))
        try:
            records.append(normalize_provider_pgn("lichess", username, exported))
        except RejectedGame:
            rejected += 1
    return records, {"fetched": len(records) + rejected, "filtered": 0, "rejected": rejected}
