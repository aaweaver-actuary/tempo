"""Leased real-game miss handoff regressions."""

from __future__ import annotations

from contextlib import contextmanager

from app import tasks
from app.services import postgres_game_misses


def test_postgres_game_misses_advance_one_event_then_handoff_with_version(monkeypatch):
    assert "game_derivation_misses" in tasks._SUPPORTED_BACKGROUND_KINDS
    published = []
    advanced = []
    enqueued = []
    completed = []
    phase_updates = []
    lease_current = True

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class Database:
        def execute(self, statement, parameters=()):
            if "FROM repertoire_decision_events" in statement:
                return Cursor({"id": "event-one", "ply": 12} if parameters[1] == -1 else None)
            if "FROM game_derivation_jobs" in statement:
                return Cursor({"derivation_version": 8, "completed_phases": 3,
                               "status": "queued"})
            if "UPDATE game_derivation_jobs" in statement:
                phase_updates.append(parameters)
            return Cursor()

    @contextmanager
    def database_connection(*, background=True):
        assert background
        yield Database()

    monkeypatch.setattr(postgres_game_misses, "background_read_connection", database_connection)
    monkeypatch.setattr(postgres_game_misses, "connection", database_connection)
    monkeypatch.setattr(postgres_game_misses, "lock_current_slice", lambda *_args: lease_current)
    monkeypatch.setattr(postgres_game_misses, "prioritize_real_game_miss",
                        lambda _database, event_id: published.append(event_id))
    monkeypatch.setattr(postgres_game_misses, "advance_task_slice_in_transaction",
                        lambda _database, _task, *, next_phase, next_payload:
                        advanced.append((next_phase, next_payload)) or True)
    monkeypatch.setattr(postgres_game_misses, "enqueue_compact_postgres_task_in_transaction",
                        lambda _database, kind, key, payload, *, priority:
                        enqueued.append((kind, key, payload, priority)))
    monkeypatch.setattr(postgres_game_misses, "complete_task_slice_in_transaction",
                        lambda _database, _task: completed.append(True) or True)

    task = {"id": "miss-task", "generation": 1, "lease_token": "live",
            "payload": {"game_id": "game-one", "derivation_version": 8,
                        "after_ply": -1, "after_id": ""}}
    assert postgres_game_misses.execute_game_miss_slice(task)
    assert published == ["event-one"]
    assert advanced[0][1]["after_id"] == "event-one"

    lease_current = False
    assert not postgres_game_misses.execute_game_miss_slice(task)
    assert published == ["event-one"]

    lease_current = True
    task["payload"] = advanced[0][1]
    assert postgres_game_misses.execute_game_miss_slice(task)
    assert phase_updates == [("game-one", 8)]
    assert enqueued == [("game_derivation_events", "game-one",
                         {"game_id": "game-one", "derivation_version": 8, "cursor": 0}, 126)]
    assert completed == [True]
