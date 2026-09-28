"""Restartable provider fetch and one-record dispatch slices for game sync."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from typing import Any
import uuid

import httpx

from ..models import GameSyncRequest
from ..postgres_store import PostgresConnection, connection
from .chesscom_client import fetch_chesscom_archive_month, fetch_chesscom_archive_urls
from .durable_tasks import (
    advance_task_slice_in_transaction, complete_task_slice_in_transaction,
    enqueue_task_in_transaction, lock_current_slice,
)
from .game_sync import USER_AGENT
from .postgres_game_sync_completion import finish_game_sync_if_complete
from .lichess_client import fetch_lichess_games_window
from .redis_admission_gate import background_lease


_MAX_LICHESS_GAMES_PER_WINDOW = 100
_MAX_CHESSCOM_GAMES_PER_MONTH = 1000


def _window_id(job_id: str, provider: str, kind: str,
               start_ms: int, end_ms: int) -> str:
    return str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"tempo:sync:{job_id}:{provider}:{kind}:{start_ms}:{end_ms}",
    ))


def _enqueue_window(database: PostgresConnection, *, job_id: str, provider: str,
                    kind: str, start_ms: int, end_ms: int,
                    source_url: str | None = None) -> str:
    identifier = _window_id(job_id, provider, kind, start_ms, end_ms)
    now = datetime.now(timezone.utc).isoformat()
    database.execute(
        """INSERT INTO game_sync_windows(
               id,job_id,provider,window_kind,window_start_ms,window_end_ms,
               source_url,created_at,updated_at
           ) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""",
        (identifier, job_id, provider, kind, start_ms, end_ms,
         source_url, now, now),
    )
    enqueue_task_in_transaction(
        database, "game_sync_window", f"{job_id}:{identifier}",
        {"job_id": job_id, "window_id": identifier}, priority=80,
    )
    return identifier


async def _fetch_window(window: dict[str, Any], request: GameSyncRequest) -> tuple[list[dict], dict]:
    username = (request.lichess_username if window["provider"] == "lichess"
                else request.chesscom_username)
    since = datetime.fromtimestamp(window["window_start_ms"] / 1000, timezone.utc)
    async with httpx.AsyncClient(timeout=45, headers={"User-Agent": USER_AGENT}) as provider_client:
        if window["window_kind"] == "archive_index":
            urls, rejected = await fetch_chesscom_archive_urls(
                username, since, provider_client,
            )
            return [{"source_url": url} for url in urls], {
                "fetched": 0, "filtered": 0, "rejected": rejected,
            }
        if window["provider"] == "lichess":
            records, counts = await fetch_lichess_games_window(
                username, window["window_start_ms"], window["window_end_ms"],
                request.speeds, request.rated_only, provider_client,
                max_games=_MAX_LICHESS_GAMES_PER_WINDOW,
            )
        else:
            records, counts = await fetch_chesscom_archive_month(
                username, window["source_url"], since, request.speeds,
                request.rated_only, provider_client,
                until=datetime.fromtimestamp(window["window_end_ms"] / 1000, timezone.utc),
            )
    return [asdict(record) for record in records], counts


def _load_window_and_request(claimed_task: dict[str, Any]) -> tuple[dict, GameSyncRequest] | None:
    with background_lease():
        with connection(read_only=True, background=True) as database:
            window_row = database.execute(
                "SELECT * FROM game_sync_windows WHERE id=?",
                (claimed_task["payload"]["window_id"],),
            ).fetchone()
            if window_row is None:
                return None
            job_row = database.execute(
                "SELECT request_json,status FROM game_sync_jobs WHERE id=?",
                (window_row["job_id"],),
            ).fetchone()
    if job_row is None or job_row["status"] not in {"queued", "running"}:
        return None
    return dict(window_row), GameSyncRequest.model_validate_json(job_row["request_json"])


def _stage_window(database: PostgresConnection, claimed_task: dict[str, Any],
                  window: dict, records: list[dict], counts: dict) -> bool:
    if not lock_current_slice(database, claimed_task):
        return False
    current = database.execute(
        "SELECT status FROM game_sync_windows WHERE id=? FOR UPDATE",
        (window["id"],),
    ).fetchone()
    if current is None or current["status"] != "planned":
        return False
    provider_page_full = (
        window["provider"] == "lichess"
        and counts["fetched"] >= _MAX_LICHESS_GAMES_PER_WINDOW
    ) or (
        window["provider"] == "chess.com"
        and len(records) >= _MAX_CHESSCOM_GAMES_PER_MONTH
    )
    if provider_page_full:
        midpoint = (window["window_start_ms"] + window["window_end_ms"]) // 2
        if midpoint <= window["window_start_ms"]:
            raise RuntimeError("Too many provider games share a one-millisecond sync window")
        for start_ms, end_ms in (
            (window["window_start_ms"], midpoint),
            (midpoint, window["window_end_ms"]),
        ):
            _enqueue_window(
                database, job_id=window["job_id"], provider=window["provider"],
                kind="games", start_ms=start_ms, end_ms=end_ms,
                source_url=window.get("source_url"),
            )
        database.execute(
            "UPDATE game_sync_windows SET status='split',updated_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), window["id"]),
        )
        return complete_task_slice_in_transaction(database, claimed_task)
    serialized = json.dumps(records, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode()).hexdigest()
    database.execute(
        """UPDATE game_sync_windows SET status='staged',records_json=?,source_digest=?,
           total_records=?,fetched_count=?,filtered_count=?,rejected_count=?,updated_at=?
           WHERE id=?""",
        (serialized, digest, len(records), counts["fetched"], counts["filtered"],
         counts["rejected"], datetime.now(timezone.utc).isoformat(), window["id"]),
    )
    if not records:
        database.execute(
            "UPDATE game_sync_windows SET status='complete' WHERE id=?", (window["id"],),
        )
        completed = complete_task_slice_in_transaction(database, claimed_task)
        if completed:
            finish_game_sync_if_complete(database, window["job_id"])
        return completed
    return advance_task_slice_in_transaction(
        database, claimed_task, next_phase="drain", next_payload=claimed_task["payload"],
    )


def _dispatch_one(database: PostgresConnection, claimed_task: dict[str, Any]) -> bool:
    if not lock_current_slice(database, claimed_task):
        return False
    window = database.execute(
        "SELECT * FROM game_sync_windows WHERE id=? FOR UPDATE",
        (claimed_task["payload"]["window_id"],),
    ).fetchone()
    if window is None or window["status"] != "staged":
        return False
    records = json.loads(window["records_json"])
    record_index = window["next_record_index"]
    if record_index >= len(records):
        raise RuntimeError("Game sync window cursor is past its staged records")
    if window["window_kind"] == "archive_index":
        archive_url = records[record_index]["source_url"]
        archive_year, archive_month = map(int, archive_url.rstrip("/").split("/")[-2:])
        month_start = datetime(archive_year, archive_month, 1, tzinfo=timezone.utc)
        next_month = datetime(archive_year + (archive_month == 12),
                              1 if archive_month == 12 else archive_month + 1,
                              1, tzinfo=timezone.utc)
        child_start_ms = max(window["window_start_ms"], int(month_start.timestamp() * 1000))
        child_end_ms = min(window["window_end_ms"], int(next_month.timestamp() * 1000))
        if child_end_ms > child_start_ms:
            _enqueue_window(
                database, job_id=window["job_id"], provider="chess.com", kind="games",
                start_ms=child_start_ms, end_ms=child_end_ms, source_url=archive_url,
            )
    else:
        enqueue_task_in_transaction(
            database, "game_sync_record",
            f"{window['job_id']}:{window['id']}:{record_index}",
            {"job_id": window["job_id"], "record": records[record_index]},
            priority=80,
        )
    next_index = record_index + 1
    database.execute(
        "UPDATE game_sync_windows SET next_record_index=?,updated_at=? WHERE id=?",
        (next_index, datetime.now(timezone.utc).isoformat(), window["id"]),
    )
    if next_index == len(records):
        database.execute(
            "UPDATE game_sync_windows SET status='complete' WHERE id=?", (window["id"],),
        )
        completed = complete_task_slice_in_transaction(database, claimed_task)
        if completed:
            finish_game_sync_if_complete(database, window["job_id"])
        return completed
    return advance_task_slice_in_transaction(
        database, claimed_task, next_phase="drain", next_payload=claimed_task["payload"],
    )


def execute_game_sync_window_slice(claimed_task: dict[str, Any]) -> bool:
    """Fetch outside PostgreSQL, then stage or dispatch one durable item."""

    loaded = _load_window_and_request(claimed_task)
    if loaded is None:
        with background_lease():
            with connection(read_only=False, background=True) as database:
                if lock_current_slice(database, claimed_task):
                    complete_task_slice_in_transaction(database, claimed_task)
        return False
    window, request = loaded
    if window["status"] == "planned":
        records, counts = asyncio.run(_fetch_window(window, request))
        with background_lease():
            with connection(read_only=False, background=True) as database:
                return _stage_window(database, claimed_task, window, records, counts)
    if window["status"] == "staged":
        with background_lease():
            with connection(read_only=False, background=True) as database:
                return _dispatch_one(database, claimed_task)
    with background_lease():
        with connection(read_only=False, background=True) as database:
            if lock_current_slice(database, claimed_task):
                complete_task_slice_in_transaction(database, claimed_task)
    return False
