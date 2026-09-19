"""Durable, low-priority orchestration for game synchronization."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import sqlite3
import time
import uuid

import chess

from ..database import connection
from ..models import GameSyncRequest
from .game_findings import refresh_game_findings
from .game_sync import sync_providers
from .repertoire_comparison import compare_games
from .activity_gate import activity_gate


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def enqueue_sync(request: GameSyncRequest) -> str:
    """Persist work before returning so navigation cannot cancel it."""
    with connection() as database:
        existing = database.execute(
            """SELECT id FROM game_sync_jobs
               WHERE status IN ('queued','running','paused','retrying')
               ORDER BY created_at LIMIT 1"""
        ).fetchone()
        if existing:
            return existing["id"]
        job_id = str(uuid.uuid4())
        now = _now()
        database.execute(
            """INSERT INTO game_sync_jobs(id,request_json,status,created_at,updated_at)
               VALUES(?,?,'queued',?,?)""",
            (job_id, request.model_dump_json(), now, now),
        )
    return job_id


def _claim_job() -> dict | None:
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        database.execute("BEGIN IMMEDIATE")
        row = database.execute(
            """SELECT * FROM game_sync_jobs WHERE status IN ('queued','retrying')
               ORDER BY created_at LIMIT 1"""
        ).fetchone()
        if not row:
            return None
        now = _now()
        changed = database.execute(
            """UPDATE game_sync_jobs SET status='running',started_at=COALESCE(started_at,?),updated_at=?
               WHERE id=? AND status IN ('queued','retrying')""",
            (now, now, row["id"]),
        ).rowcount
        return dict(row) if changed else None


def _finish_job(job_id: str, result: dict) -> None:
    now = _now()
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        database.execute(
            """UPDATE game_sync_jobs SET status='complete',result_json=?,error=NULL,
                      completed_at=?,updated_at=? WHERE id=?""",
            (json.dumps(result), now, now, job_id),
        )


def _fail_job(job_id: str, error: Exception) -> None:
    now = _now()
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        database.execute(
            """UPDATE game_sync_jobs SET status='failed',error=?,completed_at=?,updated_at=?
               WHERE id=?""",
            (str(error), now, now, job_id),
        )


def _execute_job(job: dict) -> None:
    with activity_gate.background_job("game_sync", job["id"]):
        try:
            request = GameSyncRequest.model_validate_json(job["request_json"])
            result = asyncio.run(sync_providers(request))
            changed_game_ids = result.pop("_changed_game_ids", [])
            for game_id in changed_game_ids:
                enqueue_game_derivation(game_id, background=True)
            _finish_job(job["id"], result)
        except Exception as error:  # The job error must not overwrite provider state.
            _fail_job(job["id"], error)


def enqueue_game_derivation(game_id: str, *, background: bool = False) -> None:
    if background:
        activity_gate.wait_for_foreground()
    with connection(background=background) as database:
        database.execute(
            """INSERT INTO game_derivation_jobs(game_id,status,updated_at)
               VALUES(?,'queued',?)
               ON CONFLICT(game_id) DO UPDATE SET status='queued',last_error=NULL,
                   next_attempt_at=NULL,updated_at=excluded.updated_at""",
            (game_id, _now()),
        )


def _claim_derivation() -> str | None:
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        database.execute("BEGIN IMMEDIATE")
        row = database.execute(
            """SELECT game_id FROM game_derivation_jobs WHERE status='queued'
               AND (next_attempt_at IS NULL OR next_attempt_at<=?)
               ORDER BY updated_at LIMIT 1"""
            , (_now(),)
        ).fetchone()
        if not row:
            return None
        changed = database.execute(
            """UPDATE game_derivation_jobs SET status='running',attempts=attempts+1,updated_at=?
               WHERE game_id=? AND status='queued'""",
            (_now(), row["game_id"]),
        ).rowcount
        return row["game_id"] if changed else None


def _execute_derivation(game_id: str) -> None:
    with activity_gate.background_job("game_derivation", game_id):
        try:
            _index_game_positions(game_id)
            compare_games([game_id], background=True)
            refresh_game_findings(game_id, background=True)
            activity_gate.wait_for_foreground()
            with connection(background=True) as database:
                database.execute(
                    """UPDATE game_derivation_jobs SET status='complete',last_error=NULL,next_attempt_at=NULL,updated_at=?
                       WHERE game_id=?""",
                    (_now(), game_id),
                )
        except sqlite3.OperationalError as error:
            for retry_number in range(5):
                activity_gate.wait_for_foreground()
                try:
                    with connection(background=True) as database:
                        attempts = database.execute(
                            "SELECT attempts FROM game_derivation_jobs WHERE game_id=?",
                            (game_id,),
                        ).fetchone()
                        delay_seconds = min(
                            60, 2 ** min(5, attempts[0] if attempts else 1)
                        )
                        retry_at = (
                            datetime.now(timezone.utc)
                            + timedelta(seconds=delay_seconds)
                        ).isoformat()
                        database.execute(
                            """UPDATE game_derivation_jobs SET status='queued',last_error=?,next_attempt_at=?,updated_at=?
                               WHERE game_id=?""",
                            (str(error), retry_at, _now(), game_id),
                        )
                    break
                except sqlite3.OperationalError:
                    if retry_number == 4:
                        logging.getLogger("tempo.background").exception(
                            "Could not requeue derivation job_id=%s", game_id
                        )
                    else:
                        time.sleep(0.05 * (retry_number + 1))
        except Exception as error:
            with connection(background=True) as database:
                database.execute(
                    """UPDATE game_derivation_jobs SET status='failed',last_error=?,updated_at=?
                       WHERE game_id=?""",
                    (str(error), _now(), game_id),
                )


def _index_game_positions(game_id: str) -> None:
    with connection() as database:
        game = database.execute(
            "SELECT start_fen,moves_json FROM imported_games WHERE id=?", (game_id,)
        ).fetchone()
        if not game:
            return
        board = chess.Board(game["start_fen"])
        occurrences: list[tuple[str, int, str, str | None]] = []
        for ply, move_uci in enumerate(json.loads(game["moves_json"])):
            move = chess.Move.from_uci(move_uci)
            if move not in board.legal_moves:
                break
            occurrences.append(
                (game_id, ply, " ".join(board.fen().split()[:4]), move_uci)
            )
            board.push(move)
        occurrences.append(
            (game_id, len(occurrences), " ".join(board.fen().split()[:4]), None)
        )
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        database.execute("DELETE FROM game_position_occurrences WHERE game_id=?", (game_id,))
        database.executemany(
            """INSERT INTO game_position_occurrences(game_id,ply,fen_key,move_uci)
               VALUES(?,?,?,?)""",
            occurrences,
        )


class GameSyncCoordinator:
    def __init__(self) -> None:
        self._wake_event: asyncio.Event | None = None
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        with connection() as database:
            database.execute(
                """UPDATE game_sync_jobs SET status='queued',started_at=NULL,updated_at=?
                   WHERE status IN ('running','paused')""",
                (_now(),),
            )
            database.execute(
                """UPDATE game_derivation_jobs SET status='queued',updated_at=?
                   WHERE status='running'""",
                (_now(),),
            )
        self._loop = asyncio.get_running_loop()
        self._wake_event = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        self._wake_event = None
        self._loop = None

    def wake(self) -> None:
        if self._loop and self._wake_event:
            self._loop.call_soon_threadsafe(self._wake_event.set)

    async def _run(self) -> None:
        while True:
            try:
                job = await asyncio.to_thread(_claim_job)
            except sqlite3.OperationalError:
                await asyncio.sleep(0.25)
                continue
            if job:
                await asyncio.to_thread(_execute_job, job)
                await asyncio.sleep(0)
                continue
            try:
                game_id = await asyncio.to_thread(_claim_derivation)
            except sqlite3.OperationalError:
                await asyncio.sleep(0.25)
                continue
            if game_id:
                await asyncio.to_thread(_execute_derivation, game_id)
                await asyncio.sleep(0)
                continue
            assert self._wake_event is not None
            self._wake_event.clear()
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=1)
            except TimeoutError:
                pass


coordinator = GameSyncCoordinator()


def serialize_job(row: dict | None) -> dict | None:
    if not row:
        return None
    return {
        "id": row["id"],
        "status": row["status"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "updated_at": row["updated_at"],
        "error": row["error"],
        "result": json.loads(row["result_json"]) if row["result_json"] else None,
    }
