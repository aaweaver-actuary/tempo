"""PostgreSQL health requires a usable reader, both queues, and daily progress."""

import asyncio
from contextlib import contextmanager
from datetime import date
import json
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app import command_dispatch, main, postgres_readiness, postgres_store
from app.schema_version import POSTGRES_SCHEMA_VERSION


class Cursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class Reader:
    def __init__(self, queue_row, queue_task):
        self.queue_row = queue_row
        self.queue_task = queue_task

    def execute_native(self, statement, _parameters=()):
        if "tempo_schema_migrations" in statement:
            return Cursor((POSTGRES_SCHEMA_VERSION,))
        if "FROM settings" in statement:
            return Cursor((1,))
        if "queue_projections" in statement:
            return Cursor(self.queue_row)
        return Cursor(self.queue_task)


def readiness(monkeypatch, queue_row=None, queue_task=None, worker_queues=("foreground", "background")):
    @contextmanager
    def reader():
        yield Reader(queue_row, queue_task)

    monkeypatch.setattr(postgres_readiness, "read_connection", reader)
    monkeypatch.setattr(postgres_readiness.celery_app.control, "inspect",
                        lambda timeout: SimpleNamespace(active_queues=lambda: {
                            "worker-one": [{"name": queue} for queue in worker_queues],
                        }))


def test_postgres_health_accepts_ready_queue_and_both_worker_classes(monkeypatch):
    readiness(monkeypatch, {"state": "ready", "refresh_pending": 0, "last_error": None})
    assert postgres_readiness.postgres_health()["storage"] == "postgresql"


def test_postgres_health_accepts_a_durable_queue_refresh_in_progress(monkeypatch):
    readiness(monkeypatch, {"state": "refreshing", "refresh_pending": 1, "last_error": None},
              {"state": "leased", "payload_json": json.dumps({
                  "queue_date": date.today().isoformat(),
              })})
    assert postgres_readiness.postgres_health()["status"] == "ok"


def test_postgres_health_reports_missing_worker_and_failed_queue(monkeypatch):
    readiness(monkeypatch, {"state": "ready", "refresh_pending": 0, "last_error": None},
              worker_queues=("foreground",))
    with pytest.raises(HTTPException, match="background"):
        postgres_readiness.postgres_health()
    readiness(monkeypatch, {"state": "failed", "refresh_pending": 1,
                            "last_error": "invalid source"})
    with pytest.raises(HTTPException, match="invalid source"):
        postgres_readiness.postgres_health()


def test_postgres_startup_never_starts_sqlite_writer_or_coordinator(monkeypatch):
    calls = []
    monkeypatch.setenv("TEMPO_DATABASE_READ_URL", "postgresql://reader@example/tempo")
    monkeypatch.delenv("TEMPO_DATABASE_WRITE_URL", raising=False)
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main, "initialize", lambda: calls.append("schema"))
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda *_args, **_kwargs: calls.append("queue") or {"ok": True})
    monkeypatch.setattr(main.database_writer, "start", lambda: pytest.fail("SQLite writer started"))

    async def forbidden_coordinator():
        pytest.fail("SQLite coordinator started")

    monkeypatch.setattr(main.coordinator, "start", forbidden_coordinator)

    async def run_lifespan():
        async with main.lifespan(main.app):
            calls.append("serving")

    asyncio.run(run_lifespan())
    assert calls == ["schema", "queue", "serving"]
