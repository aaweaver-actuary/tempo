"""PostgreSQL status must describe the active worker topology."""

from __future__ import annotations

from contextlib import contextmanager

from app import main


def test_postgres_activity_omits_inactive_sqlite_writer_health(monkeypatch):
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main, "list_activity", lambda **_kwargs: {
        "items": [], "counts": {"running": 0, "queued": 0,
                              "paused": 0, "failed": 0},
        "total": 0, "next_offset": None,
    })

    activity = main.system_activity()

    assert "writer" not in activity


def test_postgres_task_status_omits_inactive_sqlite_writer_health(monkeypatch):
    class Cursor:
        def __iter__(self):
            return iter(())

    class Database:
        def execute(self, _statement):
            return Cursor()

    @contextmanager
    def read_database():
        yield Database()

    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main, "list_tasks", lambda: [])
    monkeypatch.setattr(main, "read_connection", read_database)

    status = main.system_tasks()

    assert "writer" not in status


def test_sqlite_activity_retains_writer_health(monkeypatch):
    monkeypatch.setattr(main.postgres_store, "configured", lambda: False)
    monkeypatch.setattr(main, "list_activity", lambda **_kwargs: {
        "items": [], "counts": {"running": 0, "queued": 0,
                              "paused": 0, "failed": 0},
        "total": 0, "next_offset": None,
    })

    assert "writer" in main.system_activity()
