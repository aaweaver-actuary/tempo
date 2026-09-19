"""Chess.com monthly archive client with canonical account paths."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from .game_normalizer import RejectedGame, normalize_provider_pgn
from .game_record import GameRecord
from .lichess_client import ProviderRequestError


def _validate_json(response: httpx.Response, provider_label: str) -> dict:
    content_type = response.headers.get("content-type", "").lower()
    if content_type and "json" not in content_type:
        raise ProviderRequestError(f"{provider_label} returned an unexpected content type")
    try:
        payload = response.json()
    except ValueError as error:
        raise ProviderRequestError(f"{provider_label} returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise ProviderRequestError(f"{provider_label} returned an invalid response")
    return payload


async def fetch_chesscom_games(
    username: str,
    since: datetime,
    speeds: list[str],
    rated_only: bool,
    client: httpx.AsyncClient,
) -> tuple[list[GameRecord], dict[str, int]]:
    canonical_username = username.casefold()
    archive_response = await client.get(
        f"https://api.chess.com/pub/player/{canonical_username}/games/archives"
    )
    if archive_response.status_code == 404:
        raise ProviderRequestError("Chess.com username not found", 404)
    if archive_response.status_code == 429:
        raise ProviderRequestError(
            "Chess.com rate limit reached", 429, archive_response.headers.get("Retry-After")
        )
    if not archive_response.is_success:
        raise ProviderRequestError(
            f"Chess.com archive lookup failed ({archive_response.status_code})",
            archive_response.status_code,
        )
    archive_payload = _validate_json(archive_response, "Chess.com")
    cutoff_month = (since.year, since.month)
    records: list[GameRecord] = []
    fetched = filtered = rejected = 0

    for archive_url in archive_payload.get("archives", []):
        try:
            archive_year, archive_month = map(int, archive_url.rstrip("/").split("/")[-2:])
        except (TypeError, ValueError):
            rejected += 1
            continue
        if (archive_year, archive_month) < cutoff_month:
            continue
        response = await client.get(archive_url)
        if response.status_code == 429:
            raise ProviderRequestError(
                "Chess.com rate limit reached", 429, response.headers.get("Retry-After")
            )
        if not response.is_success:
            raise ProviderRequestError(
                f"Chess.com monthly archive failed ({response.status_code})", response.status_code
            )
        for raw_game in _validate_json(response, "Chess.com monthly archive").get("games", []):
            fetched += 1
            played_at = datetime.fromtimestamp(raw_game.get("end_time", 0), timezone.utc)
            speed = "classical" if raw_game.get("time_class") == "daily" else str(raw_game.get("time_class", "unknown")).lower()
            if (
                played_at < since
                or raw_game.get("rules", "chess") != "chess"
                or speed not in speeds
                or (rated_only and not raw_game.get("rated", False))
            ):
                filtered += 1
                continue
            try:
                records.append(
                    normalize_provider_pgn(
                        "chess.com",
                        username,
                        str(raw_game.get("pgn", "")),
                        provider_game_id=str(raw_game.get("uuid") or "") or None,
                        played_at=played_at.isoformat(),
                        speed=speed,
                        rated=bool(raw_game.get("rated", False)),
                        game_url=str(raw_game.get("url", "")),
                    )
                )
            except RejectedGame:
                rejected += 1
    return records, {"fetched": fetched, "filtered": filtered, "rejected": rejected}
