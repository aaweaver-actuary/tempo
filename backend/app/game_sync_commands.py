"""Durable foreground admission for PostgreSQL provider synchronization."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any
import uuid

from fastapi import HTTPException

from .command_gateway import register_command
from app.contracts.transport.game_sync import GameSyncRequest
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from tempo_legacy_database.adapters.games import synchronization as storage
from .services.durable_tasks import enqueue_task_in_transaction


def _plan_job_windows(database: PostgresConnection, job_id: str,
                      request: GameSyncRequest, created_at: str) -> None:
    """Restore active imported jobs or plan fresh provider work atomically."""

    requested_cutoff = datetime.now(timezone.utc) - timedelta(days=request.days)
    window_end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    for provider, username in (
        ("lichess", request.lichess_username.strip()),
        ("chess.com", request.chesscom_username.strip()),
    ):
        if not username:
            continue
        previous_provider = storage.lock_provider_state(database, provider)
        if (previous_provider and previous_provider["username"].casefold() == username.casefold()
                and previous_provider["retry_after"]
                and datetime.fromisoformat(previous_provider["retry_after"])
                    > datetime.now(timezone.utc)):
            raise HTTPException(429, f"{provider} sync is waiting for its rate limit to reset")
        if previous_provider and previous_provider["username"].casefold() != username.casefold():
            storage.clear_provider_cursor(database, provider)
        storage.mark_provider_syncing(database, provider, username, created_at)
        cutoff = requested_cutoff
        if not request.repair:
            newest = storage.newest_game_played_at(database, provider, username)
            if newest:
                newest_date = datetime.fromisoformat(newest)
                if newest_date <= datetime.now(timezone.utc):
                    cutoff = max(cutoff, newest_date - timedelta(days=2))
        window_start_ms = int(cutoff.timestamp() * 1000)
        window_kind = "games" if provider == "lichess" else "archive_index"
        window_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"tempo:sync:{job_id}:{provider}:{window_kind}:{window_start_ms}:{window_end_ms}",
        ))
        storage.add_sync_window(database, window_id, job_id, provider, window_kind,
                                window_start_ms, window_end_ms, created_at)
        enqueue_task_in_transaction(
            database, "game_sync_window", f"{job_id}:{window_id}",
            {"job_id": job_id, "window_id": window_id}, priority=80,
        )


def enqueue_game_sync(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    request = GameSyncRequest.model_validate(payload)
    if not request.lichess_username.strip() and not request.chesscom_username.strip():
        raise HTTPException(422, "Set a Lichess or Chess.com username in Settings")
    normalized_request = request.model_copy(update={
        "lichess_username": request.lichess_username.strip(),
        "chesscom_username": request.chesscom_username.strip(),
    })
    # A row lock cannot serialize two callers when no active job exists yet.
    storage.lock_sync_admission(database)
    existing = storage.active_sync_job(database)
    if existing:
        if existing["status"] in {"queued", "running", "retrying"} and not storage.existing_job_window(database, existing["id"]):
            recovered_request = GameSyncRequest.model_validate_json(existing["request_json"])
            _plan_job_windows(database, existing["id"], recovered_request, existing["created_at"])
            storage.mark_job_queued(database, existing["id"], datetime.now(timezone.utc).isoformat())
            return {"imported": 0, "job_id": existing["id"],
                    "status": "queued", "providers": {}}
        return {"imported": 0, "job_id": existing["id"],
                "status": existing["status"], "providers": {}}
    job_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    storage.add_sync_job(database, job_id, json.dumps(normalized_request.model_dump(mode="json")), created_at)
    _plan_job_windows(database, job_id, normalized_request, created_at)
    return {"imported": 0, "job_id": job_id, "status": "queued", "providers": {}}


register_command("games.sync.enqueue", enqueue_game_sync)
