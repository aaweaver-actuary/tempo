"""Independent, idempotent provider synchronization for the local game library."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import email.utils
import json
import sqlite3
import time

import httpx

from ..database import connection
from ..models import GameSyncRequest
from .chesscom_client import fetch_chesscom_games
from .game_record import GameRecord
from .lichess_client import ProviderRequestError, fetch_lichess_games
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
        existing = database.execute(
            "SELECT * FROM imported_games WHERE provider=? AND provider_game_id=?",
            (record.provider, record.provider_game_id),
        ).fetchone()
        if not existing:
            existing = database.execute(
                "SELECT * FROM imported_games WHERE provider=? AND lower(username)=lower(?) AND content_hash=? AND provider_game_id IS NULL",
                (record.provider, record.username, record.content_hash),
            ).fetchone()
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
        )
        if existing:
            changed = any(
                existing[column] != value
                for column, value in zip(
                    (
                        "provider_game_id", "content_hash", "username", "played_at", "speed",
                        "rated", "color", "result", "start_fen", "moves_json", "game_url", "opening_name",
                    ),
                    values,
                )
            )
            if changed:
                database.execute(
                    """UPDATE imported_games SET provider_game_id=?,content_hash=?,username=?,played_at=?,speed=?,rated=?,color=?,result=?,start_fen=?,moves_json=?,game_url=?,opening_name=? WHERE id=?""",
                    (*values, existing["id"]),
                )
                database.execute(
                    "INSERT OR IGNORE INTO game_analysis_jobs(game_id,updated_at) VALUES(?,?)",
                    (existing["id"], datetime.now(timezone.utc).isoformat()),
                )
                return "updated", existing["id"]
            database.execute(
                "INSERT OR IGNORE INTO game_analysis_jobs(game_id,updated_at) VALUES(?,?)",
                (existing["id"], datetime.now(timezone.utc).isoformat()),
            )
            return "duplicate", existing["id"]
        database.execute(
            """INSERT INTO imported_games(id,provider,provider_game_id,content_hash,username,played_at,speed,rated,color,result,start_fen,moves_json,game_url,opening_name)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"{record.provider}:{record.provider_game_id}",
                record.provider,
                *values,
            ),
        )
        database.execute(
            "INSERT INTO game_analysis_jobs(game_id,updated_at) VALUES(?,?)",
            (f"{record.provider}:{record.provider_game_id}", datetime.now(timezone.utc).isoformat()),
        )
        return "inserted", f"{record.provider}:{record.provider_game_id}"


def _starting_point(provider: str, username: str, request: GameSyncRequest) -> datetime:
    requested_cutoff = datetime.now(timezone.utc) - timedelta(days=request.days)
    if request.repair:
        return requested_cutoff
    with connection() as database:
        newest = database.execute(
            "SELECT MAX(played_at) FROM imported_games WHERE provider=? AND lower(username)=lower(?)",
            (provider, username),
        ).fetchone()[0]
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
        previous = database.execute(
            "SELECT username,retry_after FROM game_sync_state WHERE provider=?", (provider,)
        ).fetchone()
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
            database.execute(
                "UPDATE game_sync_state SET cursor=NULL,last_success_at=NULL,retry_after=NULL WHERE provider=?",
                (provider,),
            )
        database.execute(
            """INSERT INTO game_sync_state(provider,username,status,last_started_at,last_error)
               VALUES(?,?,'syncing',?,NULL)
               ON CONFLICT(provider) DO UPDATE SET username=excluded.username,status='syncing',last_started_at=excluded.last_started_at,last_error=NULL""",
            (provider, username, started_at.isoformat()),
        )
    since = _starting_point(provider, username, request)
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
            database.execute(
                """UPDATE game_sync_state SET status='idle',cursor=?,last_success_at=?,last_error=NULL,retry_after=NULL,last_result_json=? WHERE provider=?""",
                (since.isoformat(), completed_at.isoformat(), json.dumps({key: value for key, value in result.items() if not key.startswith("_")}), provider),
            )
    except (ProviderRequestError, httpx.HTTPError) as error:
        result["status"] = "error"
        result["failed"] = 1
        result["error"] = str(error) if isinstance(error, ProviderRequestError) else "The provider could not be reached. Retry when online."
        result["retry_after"] = _retry_at(error.retry_after) if isinstance(error, ProviderRequestError) else None
        activity_gate.wait_for_foreground()
        with connection(background=True) as database:
            database.execute(
                "UPDATE game_sync_state SET status='error',last_error=?,retry_after=?,last_result_json=? WHERE provider=?",
                (result["error"], result["retry_after"], json.dumps({key: value for key, value in result.items() if not key.startswith("_")}), provider),
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
