"""Named concurrency regressions for restartable PostgreSQL game indexing."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import threading
import sys
from pathlib import Path

import pytest

import chess
from psycopg.errors import DeadlockDetected, SerializationFailure

from app import tasks
from app.services import postgres_game_derivation, repertoire_game_refresh


def test_postgres_repertoire_refresh_admits_position_index_with_job_version(monkeypatch):
    enqueued: list[tuple[str, str, dict, int]] = []
    statements: list[str] = []

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class Database:
        def execute(self, statement, _parameters=()):
            statements.append(statement)
            if "FROM imported_games" in statement:
                return Cursor({"id": "game-one"})
            if "SELECT derivation_version FROM game_derivation_jobs" in statement:
                return Cursor({"derivation_version": 7})
            return Cursor()

    @contextmanager
    def write_database(*, background):
        assert background
        yield Database()

    monkeypatch.setattr(repertoire_game_refresh.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(repertoire_game_refresh.activity_gate, "wait_for_foreground", lambda: None)
    monkeypatch.setattr(repertoire_game_refresh, "connection", write_database)
    monkeypatch.setattr(repertoire_game_refresh, "lock_current_slice", lambda *_args: True)
    monkeypatch.setattr(
        repertoire_game_refresh, "enqueue_compact_postgres_task_in_transaction",
        lambda _database, kind, key, payload, *, priority:
        enqueued.append((kind, key, payload, priority)),
        raising=False,
    )

    assert repertoire_game_refresh.execute_repertoire_game_refresh_slice({
        "id": "refresh", "generation": 1, "lease_token": "live",
        "payload": {"after_game_id": ""},
    })
    assert enqueued == [
        ("game_derivation_positions", "game-one",
         {"game_id": "game-one", "derivation_version": 7, "cursor": 0}, 125),
        ("repertoire_game_refresh", "all", {"after_game_id": "game-one"}, 90),
    ]
    assert any("INSERT INTO game_derivation_jobs" in statement for statement in statements)


def test_postgres_game_position_index_yields_to_foreground_and_replays_safely(monkeypatch):
    assert "game_derivation_positions" in tasks._SUPPORTED_BACKGROUND_KINDS
    work_started = threading.Event()
    allow_work = threading.Event()
    foreground_completed = threading.Event()
    state = {"in_write": False, "lease_current": True, "version": 2}
    published = []
    advanced = []
    task = {"id": "index-task", "generation": 3, "lease_token": "live",
            "payload": {"game_id": "game-one", "derivation_version": 2, "cursor": 0}}

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class Database:
        def execute(self, statement, parameters):
            if "FROM imported_games" in statement:
                return Cursor({"start_fen": chess.STARTING_FEN, "moves_json": '["e2e4"]'})
            if "FROM game_derivation_jobs" in statement:
                return Cursor({"derivation_version": state["version"],
                               "completed_phases": 0, "status": "queued"})
            if "INSERT INTO game_position_occurrences" in statement:
                published.append(parameters)
            return Cursor()

    @contextmanager
    def read_database():
        assert not state["in_write"]
        yield Database()

    @contextmanager
    def write_database(*, background):
        assert background
        state["in_write"] = True
        try:
            yield Database()
        finally:
            state["in_write"] = False

    real_prepare = postgres_game_derivation.prepare_game_position

    def paused_prepare(*arguments):
        assert not state["in_write"]
        work_started.set()
        assert allow_work.wait(5)
        return real_prepare(*arguments)

    monkeypatch.setattr(postgres_game_derivation, "background_read_connection", read_database)
    monkeypatch.setattr(postgres_game_derivation, "connection", write_database)
    monkeypatch.setattr(postgres_game_derivation, "prepare_game_position", paused_prepare)
    monkeypatch.setattr(postgres_game_derivation, "lock_current_slice",
                        lambda _database, _task: state["lease_current"])
    monkeypatch.setattr(postgres_game_derivation, "advance_task_slice_in_transaction",
                        lambda _database, _task, *, next_phase, next_payload:
                        advanced.append((next_phase, next_payload)) or True)
    monkeypatch.setattr(postgres_game_derivation, "complete_task_slice_in_transaction",
                        lambda *_arguments: True)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(postgres_game_derivation.execute_game_position_index_slice, task)
        assert work_started.wait(2)
        assert not state["in_write"]
        foreground_completed.set()
        assert foreground_completed.is_set()
        allow_work.set()
        assert pending.result(timeout=5)
    assert len(published) == 1
    assert published[0][2] == 0
    assert advanced[0][1]["cursor"] == 1

    state["lease_current"] = False
    assert not postgres_game_derivation.execute_game_position_index_slice(task)
    assert len(published) == 1
    state["lease_current"] = True
    state["version"] = 3
    assert postgres_game_derivation.execute_game_position_index_slice(task)
    assert len(published) == 1


def test_postgres_game_position_index_retains_final_legal_position_on_bad_move():
    prepared = postgres_game_derivation.prepare_game_position(
        "game-one", chess.STARTING_FEN, ("e2e4", "e2e5", "g1f3"), 1,
    )
    assert prepared.final_position
    assert prepared.move_uci is None
    board = chess.Board()
    board.push_uci("e2e4")
    assert prepared.fen_key == " ".join(board.fen().split()[:4])


def test_postgres_derivation_lock_conflicts_yield_without_failing_task(monkeypatch):
    monkeypatch.setattr(tasks, "current_delivery", lambda _task: True)
    from contextlib import nullcontext

    claimed = {"kind": "game_derivation_positions", "id": "index-task",
               "generation": 3, "lease_token": "live", "payload": {}}
    deferred = []
    monkeypatch.setattr(tasks.activity_gate, "background_job", lambda *_args: nullcontext())
    monkeypatch.setattr(tasks, "defer_task_for_contention",
                        lambda task_id, generation, lease_token:
                        deferred.append((task_id, generation, lease_token)))
    monkeypatch.setattr(tasks, "fail_task",
                        lambda *_arguments: (_ for _ in ()).throw(
                            AssertionError("Expected lock conflict must not fail the task")))
    monkeypatch.setattr(tasks.celery_app, "send_task", lambda *_args, **_kwargs: None)
    for conflict in (DeadlockDetected, SerializationFailure):
        monkeypatch.setattr(tasks, "execute_game_position_index_slice",
                            lambda _task: (_ for _ in ()).throw(conflict("retry")))
        assert tasks.execute_background_slice.run(claimed)
    assert deferred == [("index-task", 3, "live"), ("index-task", 3, "live")]


def test_postgres_analysis_followup_checkpoints_derivation_before_index_admission(monkeypatch):
    from app import game_analysis_publication

    statements = []
    enqueued = []
    advanced = []

    class Cursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Database:
        def execute_native(self, statement, parameters):
            statements.append((statement, parameters))
            if "FROM imported_games" in statement:
                return Cursor({"analysis_version": 2, "published_analysis_generation": 2})
            if "SELECT derivation_version FROM game_derivation_jobs" in statement:
                return Cursor({"derivation_version": 4})
            return Cursor(None)

    @contextmanager
    def open_database(*, background):
        assert background
        yield Database()

    monkeypatch.setattr(game_analysis_publication, "connection", open_database)
    monkeypatch.setattr(game_analysis_publication, "lock_current_slice", lambda *_arguments: True)
    monkeypatch.setattr(game_analysis_publication, "advance_task_slice_in_transaction",
                        lambda _database, _task, *, next_phase, next_payload:
                        advanced.append((next_phase, next_payload)) or True)
    monkeypatch.setattr(game_analysis_publication,
                        "enqueue_compact_postgres_task_in_transaction",
                        lambda _database, kind, key, payload, *, priority:
                        enqueued.append((kind, key, payload, priority)))

    payload = {"game_id": "game-one", "analysis_version": 2,
               "phase": "derivation", "cursor": ""}
    task = {"id": "followup", "generation": 1, "lease_token": "live", "payload": payload}
    assert game_analysis_publication.execute_game_analysis_followup_slice(task)
    assert advanced[-1][0] == "derivation_task"
    assert any("INSERT INTO game_derivation_jobs" in statement for statement, _ in statements)
    assert not enqueued
    task["payload"] = {**payload, "phase": "derivation_task"}
    assert game_analysis_publication.execute_game_analysis_followup_slice(task)
    assert advanced[-1][0] == "threat"
    assert enqueued == [("game_derivation_positions", "game-one",
                         {"game_id": "game-one", "derivation_version": 4, "cursor": 0}, 125)]


def test_postgres_game_position_import_keeps_legacy_view_until_generation_switch():
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        from migrate_sqlite_to_postgres import COPY_TARGETS
    finally:
        sys.path.pop(0)
    migration = (Path(__file__).resolve().parents[1] / "migrations"
                 / "011_versioned_game_positions.sql").read_text()
    assert COPY_TARGETS["game_position_occurrences"] == "game_position_occurrences_legacy"
    assert "COALESCE(job.published_position_version,0)=0" in migration
    assert "job.published_position_version=staged.derivation_version" in migration


def test_postgres_game_position_index_rejects_incomplete_stage_before_visibility_switch(monkeypatch):
    task = {"id": "index-task", "generation": 3, "lease_token": "live",
            "payload": {"game_id": "game-one", "derivation_version": 2, "cursor": 0}}
    published = []

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class Database:
        def execute(self, statement, _parameters):
            if "FROM imported_games" in statement:
                return Cursor({"start_fen": chess.STARTING_FEN, "moves_json": "[]"})
            if "FROM game_derivation_jobs" in statement:
                return Cursor({"derivation_version": 2, "completed_phases": 0,
                               "status": "queued"})
            if "SELECT COUNT(*) FROM game_position_occurrences_staged" in statement:
                return Cursor((0,))
            if "published_position_version" in statement:
                published.append(statement)
            return Cursor()

    @contextmanager
    def open_database(*, background=True):
        yield Database()

    monkeypatch.setattr(postgres_game_derivation, "background_read_connection", open_database)
    monkeypatch.setattr(postgres_game_derivation, "connection", open_database)
    monkeypatch.setattr(postgres_game_derivation, "lock_current_slice", lambda *_arguments: True)
    with pytest.raises(RuntimeError, match="incomplete"):
        postgres_game_derivation.execute_game_position_index_slice(task)
    assert not published
