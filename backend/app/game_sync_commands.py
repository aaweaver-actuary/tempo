"""Durable foreground admission for PostgreSQL provider synchronization."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any
import uuid

from fastapi import HTTPException

from .command_gateway import register_command
from .models import GameSyncRequest
from .postgres_store import PostgresConnection
from .services.durable_tasks import enqueue_task_in_transaction


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
        """SELECT id,status FROM game_sync_jobs
           WHERE status IN ('queued','running','paused','retrying')
           ORDER BY created_at LIMIT 1 FOR UPDATE""",
    ).fetchone()
    if existing:
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
    enqueue_task_in_transaction(
        database, "game_sync", job_id, {"job_id": job_id}, priority=80,
    )
    return {"imported": 0, "job_id": job_id, "status": "queued", "providers": {}}


register_command("games.sync.enqueue", enqueue_game_sync)
