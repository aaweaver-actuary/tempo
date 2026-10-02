"""Named restart, admission, and cursor regressions for sync windows."""

from __future__ import annotations

from contextlib import contextmanager
import json
import threading
from types import SimpleNamespace

from app.services import postgres_game_sync_windows as windows
from app.services.postgres_game_sync_completion import finish_game_sync_if_complete


def test_game_sync_window_waits_for_foreground_before_database_read(monkeypatch):
    foreground_finished = threading.Event()
    connection_opened = threading.Event()

    @contextmanager
    def admission_gate():
        foreground_finished.wait()
        yield

    @contextmanager
    def database_connection(*, read_only, background):
        assert background
        connection_opened.set()
        yield SimpleNamespace(execute=lambda statement, parameters:
                              SimpleNamespace(fetchone=lambda: None))

    monkeypatch.setattr(windows, "background_lease", admission_gate)
    monkeypatch.setattr(windows, "connection", database_connection)
    monkeypatch.setattr(windows, "lock_current_slice", lambda database, task: False)
    results = []
    worker = threading.Thread(target=lambda: results.append(
        windows.execute_game_sync_window_slice({"payload": {"window_id": "window"}})
    ))
    worker.start()
    assert not connection_opened.wait(0.05)
    foreground_finished.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert connection_opened.is_set()
    assert results == [False]


def test_game_sync_window_splits_a_full_provider_page_without_skipping_rejections(monkeypatch):
    child_ranges = []
    completed = []
    statements = []
    monkeypatch.setattr(windows, "lock_current_slice", lambda database, task: True)
    monkeypatch.setattr(windows, "_enqueue_window", lambda database, **parameters:
                        child_ranges.append((parameters["start_ms"], parameters["end_ms"])))
    monkeypatch.setattr(windows, "complete_task_slice_in_transaction",
                        lambda database, task: completed.append(task["id"]) or True)

    class Database:
        def execute(self, statement, parameters=()):
            statements.append(statement)
            return SimpleNamespace(fetchone=lambda: {"status": "planned"})

    task = {"id": "window-task"}
    window = {"id": "window", "job_id": "job", "provider": "lichess",
              "window_start_ms": 1000, "window_end_ms": 2000}
    # A rejected game still consumes a provider page slot.
    assert windows._stage_window(
        Database(), task, window, [{}] * 99,
        {"fetched": 100, "filtered": 0, "rejected": 1},
    )
    assert child_ranges == [(1000, 1500), (1500, 2000)]
    assert completed == ["window-task"]
    assert any("status='split'" in statement for statement in statements)


def test_game_sync_window_splits_dense_chesscom_month_into_bounded_time_ranges(monkeypatch):
    children = []
    monkeypatch.setattr(windows, "lock_current_slice", lambda database, task: True)
    monkeypatch.setattr(windows, "_enqueue_window", lambda database, **parameters:
                        children.append(parameters))
    monkeypatch.setattr(windows, "complete_task_slice_in_transaction",
                        lambda database, task: True)

    class Database:
        def execute(self, statement, parameters=()):
            return SimpleNamespace(fetchone=lambda: {"status": "planned"})

    assert windows._stage_window(Database(), {"id": "task"}, {
        "id": "window", "job_id": "job", "provider": "chess.com",
        "source_url": "https://api.chess.com/pub/player/alice/games/2026/09",
        "window_start_ms": 1000, "window_end_ms": 2000,
    }, [{}] * 1000, {"fetched": 1000, "filtered": 0, "rejected": 0})
    assert [(child["start_ms"], child["end_ms"]) for child in children] == [
        (1000, 1500), (1500, 2000),
    ]
    assert all(child["source_url"].endswith("/2026/09") for child in children)


def test_game_sync_window_replay_dispatches_each_staged_record_once(monkeypatch):
    dispatched = []
    active = {"lease": "first"}
    window = {
        "id": "window", "job_id": "job", "provider": "lichess",
        "window_kind": "games", "status": "staged", "next_record_index": 0,
        "records_json": json.dumps([{"provider_game_id": "game-1"}]),
    }
    monkeypatch.setattr(windows, "lock_current_slice",
                        lambda database, task: task["lease_token"] == active["lease"])
    monkeypatch.setattr(windows, "enqueue_task_in_transaction",
                        lambda database, kind, key, payload, **options:
                        dispatched.append((kind, key, payload)))
    monkeypatch.setattr(windows, "complete_task_slice_in_transaction",
                        lambda database, task: active.update(lease="complete") or True)
    monkeypatch.setattr(windows, "finish_game_sync_if_complete",
                        lambda database, job_id: False)

    class Database:
        def execute(self, statement, parameters=()):
            if statement.startswith("SELECT * FROM game_sync_windows"):
                return SimpleNamespace(fetchone=lambda: window)
            return SimpleNamespace(rowcount=1)

    task = {"id": "task", "lease_token": "first", "payload": {"window_id": "window"}}
    assert windows._dispatch_one(Database(), task)
    assert not windows._dispatch_one(Database(), task)
    assert dispatched == [(
        "game_sync_record", "job:window:0",
        {"job_id": "job", "record": {"provider_game_id": "game-1"}},
    )]


def test_game_sync_completion_waits_for_every_window_and_record_receipt():
    statements = []
    unfinished = {"window": True, "record": True}

    class Database:
        def execute(self, statement, parameters=()):
            statements.append(statement)
            if "FROM game_sync_jobs" in statement:
                return SimpleNamespace(fetchone=lambda: {
                    "status": "running", "request_json": json.dumps({
                        "lichess_username": "alice",
                    }), "result_json": json.dumps({"providers": {
                        "lichess": {"inserted": 1, "updated": 0, "duplicates": 0},
                    }}),
                })
            if "status NOT IN ('complete','split')" in statement:
                return SimpleNamespace(fetchone=lambda: 1 if unfinished["window"] else None)
            if "kind='game_sync_record'" in statement:
                return SimpleNamespace(fetchone=lambda: 1 if unfinished["record"] else None)
            if "SUM(fetched_count)" in statement:
                return SimpleNamespace(fetchall=lambda: [{
                    "provider": "lichess", "fetched": 1, "filtered": 0,
                    "rejected": 0, "earliest_ms": 1000,
                }])
            return SimpleNamespace(rowcount=1)

    database = Database()
    assert not finish_game_sync_if_complete(database, "job")
    unfinished["window"] = False
    assert not finish_game_sync_if_complete(database, "job")
    unfinished["record"] = False
    assert finish_game_sync_if_complete(database, "job")
    assert sum("UPDATE game_sync_jobs SET status='complete'" in statement
               for statement in statements) == 1


def test_game_sync_terminal_task_failure_marks_job_failed(monkeypatch):
    from app.services import durable_tasks

    statements = []
    monkeypatch.setattr(durable_tasks, "submit_background_write",
                        lambda operation, *, label: operation(Database()))
    monkeypatch.setattr(durable_tasks, "_record_event", lambda *args, kind=None: None)

    class Database:
        def execute(self, statement, parameters=()):
            statements.append(statement)
            if statement.startswith("SELECT attempt_count,max_attempts"):
                return SimpleNamespace(fetchone=lambda: {
                    "attempt_count": 5, "max_attempts": 5,
                    "kind": "game_sync_window", "payload_json": json.dumps({
                        "job_id": "job", "window_id": "window",
                    }),
                })
            if statement.startswith("SELECT provider FROM game_sync_windows"):
                return SimpleNamespace(fetchone=lambda: {"provider": "lichess"})
            return SimpleNamespace(rowcount=1)

    result = durable_tasks.fail_task("task", 1, "lease", RuntimeError("provider unavailable"))
    assert result["state"] == "failed"
    assert any("UPDATE game_sync_jobs SET status='failed'" in statement
               for statement in statements)
    assert any("UPDATE game_sync_state SET status='error'" in statement
               for statement in statements)
