"""Durable, low-priority orchestration for game synchronization."""

from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import logging
import sqlite3
import time
from typing import Callable
import uuid

import chess

from ..database import connection
from ..models import GameSyncRequest
from .game_findings import refresh_game_findings
from .real_game_feedback import apply_real_game_misses
from .gameplay_events import refresh_gameplay_events
from .game_sync import sync_providers
from .repertoire_comparison import compare_games
from .activity_gate import activity_gate
from .repertoire_coverage import claim_coverage_node, execute_coverage_node
from .introduction_priorities import (
    calculate_priority_records,
    claim_priority_refresh,
    enqueue_priority_refreshes_for_game,
    execute_priority_refresh,
    load_priority_calculation_input,
    publish_priority_records,
)
from .statistics import claim_daily_snapshot, execute_daily_snapshot, refresh_game_features
from .repertoire_integrity import (
    claim_integrity_slice,
    execute_integrity_slice,
    enqueue_integrity_scans,
    requeue_integrity_slice,
)
from .durable_tasks import claim_task, complete_task, fail_task, requeue_interrupted_tasks
from .opening_graph import (
    calculate_opening_graph_artifacts,
    prepare_opening_graph_rebuild,
    publish_opening_graph_rebuild,
)


_durable_task_handlers: dict[str, Callable[[dict], None]] = {}
_maintenance_handlers: list[Callable[[], None]] = []


def register_durable_task_handler(kind: str, handler: Callable[[dict], None]) -> None:
    _durable_task_handlers[kind] = handler


def register_maintenance_handler(handler: Callable[[], None]) -> None:
    _maintenance_handlers.append(handler)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def enqueue_sync(request: GameSyncRequest, *, background: bool = False) -> str:
    """Persist work before returning so navigation cannot cancel it."""
    with connection(background=background) as database:
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
            """UPDATE game_derivation_jobs SET status='running',phase='claimed',attempts=attempts+1,updated_at=?
               WHERE game_id=? AND status='queued'""",
            (_now(), row["game_id"]),
        ).rowcount
        return row["game_id"] if changed else None


def _set_derivation_phase(game_id: str, phase: str) -> None:
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        database.execute(
            "UPDATE game_derivation_jobs SET phase=?,updated_at=? WHERE game_id=? AND status='running'",
            (phase, _now(), game_id),
        )


def _run_derivation_phase(
    game_id: str, phase: str, operation: Callable[[], None]
) -> None:
    _set_derivation_phase(game_id, phase)
    phase_started = time.perf_counter()
    try:
        operation()
    finally:
        logging.getLogger("tempo.background").info(
            "derivation phase game_id=%s phase=%s duration=%.3fs",
            game_id,
            phase,
            time.perf_counter() - phase_started,
        )


def _execute_derivation(game_id: str) -> None:
    with activity_gate.background_job("game_derivation", game_id):
        try:
            _run_derivation_phase(
                game_id, "indexing_positions", lambda: _index_game_positions(game_id)
            )
            _run_derivation_phase(
                game_id,
                "comparing_repertoire",
                lambda: compare_games([game_id], background=True),
            )
            _run_derivation_phase(
                game_id,
                "refreshing_findings",
                lambda: refresh_game_findings(game_id, background=True),
            )
            _run_derivation_phase(
                game_id,
                "applying_real_game_misses",
                lambda: apply_real_game_misses(game_id, background=True),
            )
            _run_derivation_phase(
                game_id,
                "refreshing_events",
                lambda: refresh_gameplay_events(game_id, background=True),
            )
            _run_derivation_phase(
                game_id,
                "refreshing_features",
                lambda: refresh_game_features(game_id, background=True),
            )
            _run_derivation_phase(
                game_id,
                "enqueueing_priorities",
                lambda: enqueue_priority_refreshes_for_game(game_id, background=True),
            )
            activity_gate.wait_for_foreground()
            with connection(background=True) as database:
                database.execute(
                    """UPDATE game_derivation_jobs SET status='complete',phase=NULL,last_error=NULL,next_attempt_at=NULL,updated_at=?
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
                            """UPDATE game_derivation_jobs SET status='queued',phase=NULL,last_error=?,next_attempt_at=?,updated_at=?
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
                    """UPDATE game_derivation_jobs SET status='failed',phase=NULL,last_error=?,updated_at=?
                       WHERE game_id=?""",
                    (str(error), _now(), game_id),
                )


def _index_game_positions(game_id: str) -> None:
    with connection(background=True) as database:
        game_row = database.execute(
            "SELECT start_fen,moves_json FROM imported_games WHERE id=?", (game_id,)
        ).fetchone()
        game = dict(game_row) if game_row else None
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
        self._next_source = 0
        self._process_pool: ProcessPoolExecutor | None = None

    async def start(self) -> None:
        requeue_interrupted_tasks()
        _requeue_interrupted_background_work()
        enqueue_integrity_scans(stale_only=True)
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
        if self._process_pool is not None:
            self._process_pool.shutdown(wait=True, cancel_futures=True)
            self._process_pool = None

    def wake(self) -> None:
        if self._loop and self._wake_event:
            self._loop.call_soon_threadsafe(self._wake_event.set)

    async def _run(self) -> None:
        sources = (
            "durable",
            "sync",
            "derivation",
            "coverage",
            "integrity",
            "statistics",
            "priority",
        )
        while True:
            worked = False
            for offset in range(len(sources)):
                source_index = (self._next_source + offset) % len(sources)
                source = sources[source_index]
                try:
                    if source == "durable":
                        item = await asyncio.to_thread(claim_task)
                    elif source == "sync":
                        item = await asyncio.to_thread(_claim_job)
                    elif source == "derivation":
                        item = await asyncio.to_thread(_claim_derivation)
                    elif source == "coverage":
                        item = await asyncio.to_thread(claim_coverage_node)
                    elif source == "integrity":
                        item = await asyncio.to_thread(claim_integrity_slice)
                    elif source == "statistics":
                        item = await asyncio.to_thread(claim_daily_snapshot)
                    else:
                        item = await asyncio.to_thread(claim_priority_refresh)
                except sqlite3.OperationalError:
                    await asyncio.sleep(0.25)
                    continue
                if not item:
                    continue
                self._next_source = (source_index + 1) % len(sources)
                slice_started = time.perf_counter()
                try:
                    if source == "durable":
                        handler = _durable_task_handlers.get(item["kind"])
                        if handler is None:
                            raise RuntimeError(f"No handler registered for task kind {item['kind']}")
                        try:
                            if item["kind"] == "opening_graph_rebuild":
                                rebuild_input = await asyncio.to_thread(
                                    prepare_opening_graph_rebuild, item
                                )
                                if self._process_pool is None:
                                    self._process_pool = ProcessPoolExecutor(max_workers=1)
                                graph_artifacts = await asyncio.get_running_loop().run_in_executor(
                                    self._process_pool,
                                    calculate_opening_graph_artifacts,
                                    rebuild_input,
                                )
                                await asyncio.to_thread(
                                    publish_opening_graph_rebuild,
                                    item,
                                    graph_artifacts,
                                )
                            else:
                                await asyncio.to_thread(handler, item)
                            await asyncio.to_thread(
                                complete_task,
                                item["id"],
                                item["generation"],
                                item["lease_token"],
                            )
                        except Exception as error:
                            await asyncio.to_thread(
                                fail_task,
                                item["id"],
                                item["generation"],
                                item["lease_token"],
                                error,
                            )
                    elif source == "sync":
                        await asyncio.to_thread(_execute_job, item)
                    elif source == "derivation":
                        await asyncio.to_thread(_execute_derivation, item)
                    elif source == "coverage":
                        await asyncio.to_thread(execute_coverage_node, item)
                    elif source == "integrity":
                        await asyncio.to_thread(execute_integrity_slice, item)
                    elif source == "statistics":
                        await asyncio.to_thread(execute_daily_snapshot, item)
                    else:
                        calculation_input = await asyncio.to_thread(
                            load_priority_calculation_input, item
                        )
                        if self._process_pool is None:
                            self._process_pool = ProcessPoolExecutor(max_workers=1)
                        records = await asyncio.get_running_loop().run_in_executor(
                            self._process_pool,
                            calculate_priority_records,
                            calculation_input,
                        )
                        await asyncio.to_thread(publish_priority_records, item, records)
                except sqlite3.OperationalError as error:
                    if source == "integrity":
                        await asyncio.to_thread(requeue_integrity_slice, item, error)
                    else:
                        logging.getLogger("tempo.background").warning(
                            "background slice contention source=%s item=%s error=%s",
                            source,
                            item,
                            error,
                        )
                except Exception as error:
                    if source == "integrity":
                        await asyncio.to_thread(requeue_integrity_slice, item, error)
                    else:
                        logging.getLogger("tempo.background").exception(
                            "background slice failed source=%s item=%s",
                            source,
                            item,
                        )
                logging.getLogger("tempo.background").info(
                    "background slice source=%s item=%s slice_duration=%.3fs retry_count=%s preempted=%s",
                    source,
                    item.get("id", item.get("game_id", item.get("repertoire_id", item))) if isinstance(item, dict) else item,
                    time.perf_counter() - slice_started,
                    item.get("attempts", 0) if isinstance(item, dict) else 0,
                    activity_gate.foreground_waiting,
                )
                worked = True
                await asyncio.sleep(0)
                break
            if worked:
                continue
            for maintenance_handler in _maintenance_handlers:
                try:
                    await asyncio.to_thread(maintenance_handler)
                except Exception:
                    logging.getLogger("tempo.background").exception(
                        "background maintenance handler failed"
                    )
            assert self._wake_event is not None
            self._wake_event.clear()
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=1)
            except TimeoutError:
                pass


def _requeue_interrupted_background_work() -> None:
    now = _now()
    with connection() as database:
        database.execute(
            """UPDATE game_sync_jobs SET status='queued',started_at=NULL,updated_at=?
               WHERE status IN ('running','paused')""",
            (now,),
        )
        database.execute(
            """UPDATE game_derivation_jobs SET status='queued',phase=NULL,updated_at=?
               WHERE status='running'""",
            (now,),
        )
        database.execute(
            """UPDATE repertoire_coverage_nodes SET explorer_status='queued',updated_at=?
               WHERE explorer_status='running'""",
            (now,),
        )
        database.execute(
            """UPDATE repertoire_integrity_jobs SET status='queued',updated_at=?
               WHERE status IN ('running','finalizing')""",
            (now,),
        )
        database.execute(
            """UPDATE repertoire_integrity_state SET scan_status='queued'
               WHERE scan_status IN ('running','retrying')"""
        )
        database.execute(
            """UPDATE repertoire_priority_jobs
               SET status='queued',next_attempt_at=?,updated_at=?
               WHERE status='running'""",
            (now, now),
        )


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
