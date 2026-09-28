"""Durable foreground admission for PostgreSQL provider synchronization."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any
import uuid

from fastapi import HTTPException

from .command_gateway import register_command
from .models import GameSyncRequest
from .postgres_store import PostgresConnection
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
        previous_provider = database.execute(
            "SELECT username,retry_after FROM game_sync_state WHERE provider=? FOR UPDATE",
            (provider,),
        ).fetchone()
        if (previous_provider and previous_provider["username"].casefold() == username.casefold()
                and previous_provider["retry_after"]
                and datetime.fromisoformat(previous_provider["retry_after"])
                    > datetime.now(timezone.utc)):
            raise HTTPException(429, f"{provider} sync is waiting for its rate limit to reset")
        if previous_provider and previous_provider["username"].casefold() != username.casefold():
            database.execute(
                """UPDATE game_sync_state SET cursor=NULL,last_success_at=NULL,
                   retry_after=NULL WHERE provider=?""",
                (provider,),
            )
        database.execute(
            """INSERT INTO game_sync_state(provider,username,status,last_started_at,last_error)
               VALUES(?,?,'syncing',?,NULL)
               ON CONFLICT(provider) DO UPDATE SET username=excluded.username,
                   status='syncing',last_started_at=excluded.last_started_at,last_error=NULL""",
            (provider, username, created_at),
        )
        cutoff = requested_cutoff
        if not request.repair:
            newest = database.execute(
                """SELECT MAX(played_at) FROM imported_games
                   WHERE provider=? AND lower(username)=lower(?)""",
                (provider, username),
            ).fetchone()[0]
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
        database.execute(
            """INSERT INTO game_sync_windows(
                   id,job_id,provider,window_kind,window_start_ms,window_end_ms,
                   created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""",
            (window_id, job_id, provider, window_kind, window_start_ms,
             window_end_ms, created_at, created_at),
        )
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
    database.raw.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        ("tempo:game-sync-admission",),
    )
    existing = database.execute(
        """SELECT id,status,request_json,created_at FROM game_sync_jobs
           WHERE status IN ('queued','running','paused','retrying')
           ORDER BY created_at LIMIT 1 FOR UPDATE""",
    ).fetchone()
    if existing:
        if existing["status"] in {"queued", "running", "retrying"} and not database.execute(
            "SELECT 1 FROM game_sync_windows WHERE job_id=? LIMIT 1", (existing["id"],),
        ).fetchone():
            recovered_request = GameSyncRequest.model_validate_json(existing["request_json"])
            _plan_job_windows(database, existing["id"], recovered_request, existing["created_at"])
            database.execute(
                "UPDATE game_sync_jobs SET status='queued',updated_at=? WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), existing["id"]),
            )
            return {"imported": 0, "job_id": existing["id"],
                    "status": "queued", "providers": {}}
        return {"imported": 0, "job_id": existing["id"],
                "status": existing["status"], "providers": {}}
    job_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    database.execute(
        """INSERT INTO game_sync_jobs(id,request_json,status,created_at,updated_at)
           VALUES(?,?,'queued',?,?)""",
        (job_id, json.dumps(normalized_request.model_dump(mode="json")),
         created_at, created_at),
    )
    _plan_job_windows(database, job_id, normalized_request, created_at)
    return {"imported": 0, "job_id": job_id, "status": "queued", "providers": {}}


register_command("games.sync.enqueue", enqueue_game_sync)
