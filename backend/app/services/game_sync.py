"""Independent, idempotent provider synchronization for the local game library."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import email.utils
import json
import sqlite3
import time

import httpx

from tempo_legacy_database.connections import connection
from tempo_legacy_database.adapters.games.imports import legacy as storage
from tempo_legacy_database.adapters.games.sync import records as sync_storage
from app.contracts.transport.game_sync import GameSyncRequest
from app.games.providers.chesscom import fetch_chesscom_games
from app.games.records import GameRecord
from app.games.providers.lichess import ProviderRequestError, fetch_lichess_games
from .activity_gate import activity_gate


USER_AGENT = "Tempo local chess trainer/1.0 (game sync)"
OVERLAP = timedelta(days=2)


def _empty_result(provider: str, username: str) -> dict:
    return {
        "provider": provider,
        "username": username,
        "status": "idle",
        "fetched": 0,
        "inserted": 0,
        "updated": 0,
        "duplicates": 0,
        "filtered": 0,
        "rejected": 0,
        "failed": 0,
        "error": None,
        "retry_after": None,
    }


def _retry_at(value: str | None) -> str | None:
    if not value:
        return None
    try:
        seconds = max(0, int(value))
        return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
            return parsed.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            return None


def _persist_game(record: GameRecord) -> tuple[str, str]:
    for retry_number in range(5):
        activity_gate.wait_for_foreground()
        try:
            return _persist_game_once(record)
        except sqlite3.OperationalError:
            if retry_number == 4:
                raise
            time.sleep(0.05 * (2**retry_number))
    raise RuntimeError("Unreachable game persistence retry state")


def _persist_game_once(record: GameRecord) -> tuple[str, str]:
    with connection(background=True) as database:
        existing = storage.imported_provider_game(database, record.provider, record.provider_game_id)
        if not existing:
            existing = storage.imported_legacy_game(
                database, record.provider, record.username, record.content_hash,
            )
        values = (
            record.provider_game_id,
            record.content_hash,
            record.username,
            record.played_at,
            record.speed,
            int(record.rated),
            record.color,
            record.result,
            record.start_fen,
            json.dumps(record.uci_moves),
            record.game_url,
            record.opening_name,
            record.player_rating,
            record.opponent_rating,
            record.rating_change,
            record.time_control,
        )
        if existing:
            changed = any(
                existing[column] != value
                for column, value in zip(
                    (
                        "provider_game_id", "content_hash", "username", "played_at", "speed",
                        "rated", "color", "result", "start_fen", "moves_json", "game_url", "opening_name",
                        "player_rating", "opponent_rating", "rating_change", "time_control",
                    ),
                    values,
                )
            )
            if changed:
                storage.update_imported_game(database, existing["id"], values)
                storage.ensure_analysis_job(database, existing["id"], datetime.now(timezone.utc).isoformat())
                sync_storage.invalidate_day_snapshot(database, record.played_at[:10])
                sync_storage.invalidate_day_insights(database, record.played_at[:10])
                return "updated", existing["id"]
            storage.ensure_analysis_job(database, existing["id"], datetime.now(timezone.utc).isoformat())
            return "duplicate", existing["id"]
        storage.insert_imported_game(
            database, f"{record.provider}:{record.provider_game_id}", record.provider, values,
        )
        storage.insert_analysis_job(
            database, f"{record.provider}:{record.provider_game_id}", datetime.now(timezone.utc).isoformat(),
        )
        sync_storage.invalidate_day_snapshot(database, record.played_at[:10])
        sync_storage.invalidate_day_insights(database, record.played_at[:10])
        return "inserted", f"{record.provider}:{record.provider_game_id}"


def _starting_point(provider: str, username: str, request: GameSyncRequest, *, background: bool = False) -> datetime:
    requested_cutoff = datetime.now(timezone.utc) - timedelta(days=request.days)
    if request.repair:
        return requested_cutoff
    with connection(background=background) as database:
        newest = storage.newest_provider_game_time(database, provider, username)
    if not newest:
        return requested_cutoff
    return max(requested_cutoff, datetime.fromisoformat(newest) - OVERLAP)


async def _sync_provider(
    provider: str,
    username: str,
    request: GameSyncRequest,
    client: httpx.AsyncClient,
) -> dict:
    result = _empty_result(provider, username)
    started_at = datetime.now(timezone.utc)
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        previous = storage.provider_retry_state(database, provider)
        if (
            previous
            and previous["username"].casefold() == username.casefold()
            and previous["retry_after"]
            and datetime.fromisoformat(previous["retry_after"]) > started_at
        ):
            result["status"] = "error"
            result["failed"] = 1
            result["retry_after"] = previous["retry_after"]
            result["error"] = f"{provider} sync is waiting for its rate limit to reset"
            return result
        if previous and previous["username"].casefold() != username.casefold():
            storage.reset_provider_cursor(database, provider)
        storage.mark_provider_syncing(database, provider, username, started_at.isoformat())
    since = _starting_point(provider, username, request, background=True)
    try:
        if provider == "lichess":
            records, counts = await fetch_lichess_games(
                username, int(since.timestamp() * 1000), request.speeds, request.rated_only, client
            )
        else:
            records, counts = await fetch_chesscom_games(
                username, since, request.speeds, request.rated_only, client
            )
        result.update(counts)
        changed_game_ids: list[str] = []
        for record in records:
            outcome, persisted_game_id = _persist_game(record)
            result[{"inserted": "inserted", "updated": "updated", "duplicate": "duplicates"}[outcome]] += 1
            if outcome in {"inserted", "updated"}:
                changed_game_ids.append(persisted_game_id)
        result["_changed_game_ids"] = changed_game_ids
        result["status"] = "idle"
        completed_at = datetime.now(timezone.utc)
        activity_gate.wait_for_foreground()
        with connection(background=True) as database:
            storage.save_provider_success(
                database, provider, since.isoformat(), completed_at.isoformat(),
                json.dumps({key: value for key, value in result.items() if not key.startswith("_")}),
            )
    except (ProviderRequestError, httpx.HTTPError) as error:
        result["status"] = "error"
        result["failed"] = 1
        result["error"] = str(error) if isinstance(error, ProviderRequestError) else "The provider could not be reached. Retry when online."
        result["retry_after"] = _retry_at(error.retry_after) if isinstance(error, ProviderRequestError) else None
        activity_gate.wait_for_foreground()
        with connection(background=True) as database:
            storage.save_provider_failure(
                database, provider, result["error"], result["retry_after"],
                json.dumps({key: value for key, value in result.items() if not key.startswith("_")}),
            )
    return result


async def sync_providers(request: GameSyncRequest) -> dict:
    provider_accounts = (
        ("lichess", request.lichess_username.strip()),
        ("chess.com", request.chesscom_username.strip()),
    )
    results: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=45, headers={"User-Agent": USER_AGENT}) as client:
        for provider, username in provider_accounts:
            if username:
                results[provider] = await _sync_provider(provider, username, request, client)
    changed_game_ids = [
        game_id
        for item in results.values()
        for game_id in item.pop("_changed_game_ids", [])
    ]
    return {
        "imported": sum(item["inserted"] for item in results.values()),
        "cached": True,
        "incremental": not request.repair,
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "providers": results,
        "_changed_game_ids": changed_game_ids,
    }
