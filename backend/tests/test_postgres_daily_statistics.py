"""Receipt and lease regressions for daily PostgreSQL statistics."""

from __future__ import annotations

from contextlib import contextmanager

from app import command_dispatch, main, postgres_store, statistics_commands, tasks
from app.services import postgres_daily_statistics


class Cursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


def test_postgres_daily_statistics_request_admits_same_day_task(monkeypatch):
    queued = []
    writes = []

    class Database:
        def execute(self, statement, parameters=()):
            writes.append((statement, parameters))
            return Cursor()

    monkeypatch.setattr(statistics_commands,
                        "enqueue_compact_postgres_task_in_transaction",
                        lambda _database, kind, key, payload, *, priority:
                        queued.append((kind, key, payload, priority)))
    assert statistics_commands.request_daily_statistics_refresh(
        Database(), {"local_day": "2026-09-28"},
    ) == {"local_day": "2026-09-28", "status": "queued"}
    assert writes[0][1][0] == "2026-09-28"
    assert queued == [("daily_statistics", "2026-09-28",
                       {"local_day": "2026-09-28"}, 120)]

    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                        {"command": name, "payload": payload, "operation_id": idempotency_key})
    assert main.refresh_chess_statistics_day("2026-09-28", "save-one") == {
        "command": "statistics.daily.refresh",
        "payload": {"local_day": "2026-09-28"},
        "operation_id": "save-one",
    }


def test_postgres_daily_statistics_publishes_only_under_current_lease(monkeypatch):
    assert "daily_statistics" in tasks._SUPPORTED_BACKGROUND_KINDS
    writes = []
    completed = []
    lease_current = True

    class Database:
        def execute(self, statement, parameters=()):
            writes.append((statement, parameters))
            return Cursor()

    @contextmanager
    def write_database(*, background):
        assert background
        yield Database()

    monkeypatch.setattr(postgres_daily_statistics, "_prepare_daily_statistics",
                        lambda _day: {"local_day": "2026-09-28", "status": "complete",
                                      "games": 20, "score": 0.5,
                                      "tactical_found": 1,
                                      "tactical_opportunities": 10})
    monkeypatch.setattr(postgres_daily_statistics, "connection", write_database)
    monkeypatch.setattr(postgres_daily_statistics, "lock_current_slice",
                        lambda *_args: lease_current)
    monkeypatch.setattr(postgres_daily_statistics, "complete_task_slice_in_transaction",
                        lambda _database, _task: completed.append(True) or True)

    task = {"id": "statistics", "generation": 1, "lease_token": "live",
            "payload": {"local_day": "2026-09-28"}}
    assert postgres_daily_statistics.execute_postgres_daily_statistics_slice(task)
    assert sum("INSERT OR IGNORE INTO daily_chess_insights" in statement
               for statement, _ in writes) == 2
    assert completed == [True]
    written_count = len(writes)
    lease_current = False
    assert not postgres_daily_statistics.execute_postgres_daily_statistics_slice(task)
    assert len(writes) == written_count
