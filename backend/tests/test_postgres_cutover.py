"""Named regressions for the PostgreSQL command and admission boundaries."""

from __future__ import annotations

import threading
from contextlib import contextmanager, nullcontext
import sqlite3

from pathlib import Path

from celery.exceptions import TimeoutError as CeleryTimeout
from fastapi import HTTPException
from kombu.exceptions import OperationalError as BrokerUnavailable
import pytest

from app import command_dispatch
from app.command_gateway import CommandConflict, request_digest
from app.postgres_store import TempoRow, postgres_sql
from app.services import redis_admission_gate


def test_postgres_cutover_schema_keeps_json_array_length_available(tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from generate_postgres_schema import generate_schema

    source = tmp_path / "source.db"
    with sqlite3.connect(source) as database:
        database.execute("CREATE TABLE example(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    schema = generate_schema(source)
    assert "CREATE FUNCTION json_array_length(document TEXT)" in schema
    assert "jsonb_array_length(document::jsonb)" in schema


def test_postgres_cutover_study_chapter_routes_dispatch_named_commands(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main, study_routes

    dispatched: list[tuple[str, dict, str | None]] = []

    def record_command(name, payload, *, idempotency_key):
        dispatched.append((name, payload, idempotency_key))
        return {"accepted": name}

    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(study_routes, "dispatch_command", record_command)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    client = TestClient(main.app)
    headers = {"Idempotency-Key": "study-command-1"}
    requests = (
        ("post", "/api/studies/study-1/chapters", {"title": "Chapter"}, "studies.chapters.create"),
        ("put", "/api/studies/study-1/chapters/order", ["chapter-1"], "studies.chapters.reorder"),
        ("patch", "/api/studies/study-1/chapters/chapter-1", {"title": "Renamed"}, "studies.chapters.rename"),
        ("post", "/api/studies/study-1/links", {
            "source_position_id": "position-1", "target_position_id": "position-2",
            "relation": "illustrates",
        }, "studies.links.create"),
    )
    for method, path, body, command_name in requests:
        response = getattr(client, method)(path, json=body, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json() == {"accepted": command_name}
    assert [name for name, _, _ in dispatched] == [item[3] for item in requests]
    assert all(key == "study-command-1" for _, _, key in dispatched)


def test_postgres_cutover_queue_fail_and_bury_dispatch_foreground_commands(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    dispatched: list[tuple[str, dict, str | None]] = []

    def record_command(name, payload, *, idempotency_key):
        dispatched.append((name, payload, idempotency_key))
        return {"accepted": name}

    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(command_dispatch, "dispatch_command", record_command)
    client = TestClient(main.app)
    for action, command_name in (("fail", "queue.attempt_failed"), ("bury", "queue.bury")):
        response = client.post(
            f"/api/queue/entries/42/{action}",
            headers={"Idempotency-Key": f"queue-42-{action}"},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"accepted": command_name}
    assert dispatched == [
        ("queue.attempt_failed", {"entry_id": 42}, "queue-42-fail"),
        ("queue.bury", {"entry_id": 42}, "queue-42-bury"),
    ]


def test_postgres_cutover_translates_placeholders_and_rejects_runtime_pragma():
    assert postgres_sql("SELECT id FROM cards WHERE id=?") == "SELECT id FROM cards WHERE id = %s"
    assert postgres_sql("INSERT OR IGNORE INTO settings(id) VALUES(?)").endswith(
        "ON CONFLICT DO NOTHING"
    )
    with pytest.raises(ValueError, match="SQLite PRAGMA"):
        postgres_sql("PRAGMA foreign_keys = ON")


def test_postgres_cutover_conflict_target_does_not_include_null_ordering():
    statement = postgres_sql(
        "INSERT INTO queue_projections(queue_date,state) VALUES(?,'refreshing') "
        "ON CONFLICT(queue_date) DO UPDATE SET state=excluded.state"
    )
    assert "ON CONFLICT(queue_date) DO UPDATE" in statement
    assert "NULLS FIRST" not in statement


def test_postgres_cutover_row_supports_mapping_and_sqlite_value_iteration():
    row = TempoRow(("total", "unread_count"), (3, 2))
    assert dict(row) == {"total": 3, "unread_count": 2}
    assert tuple(row) == (3, 2)
    assert row[0] == row["total"] == 3


def test_postgres_cutover_idempotency_digest_is_payload_order_independent():
    first = request_digest("review.submit", {"card_id": "a", "outcome": "again"})
    assert first == request_digest("review.submit", {"outcome": "again", "card_id": "a"})
    assert first != request_digest("review.submit", {"card_id": "a", "outcome": "correct"})


def test_postgres_cutover_ambiguous_timeout_stays_pending(monkeypatch):
    class PendingTask:
        state = "PENDING"

        def get(self, **_):
            raise CeleryTimeout()

    monkeypatch.setattr(command_dispatch.celery_app, "send_task", lambda *_, **__: PendingTask())
    monkeypatch.setattr(
        command_dispatch, "read_operation",
        lambda operation_id, **_: {"operation_id": operation_id, "state": "pending"},
    )
    response = command_dispatch.dispatch_command(
        "review.submit", {"card_id": "a"}, idempotency_key="review-a"
    )
    assert response.status_code == 202
    assert response.headers["location"] == "/api/operations/review-a"
    assert b'"state":"pending"' in response.body


def test_postgres_cutover_result_store_outage_checks_receipt_before_reporting_pending(monkeypatch):
    class ResultStoreUnavailableTask:
        state = "PENDING"

        def get(self, **_):
            from redis import ConnectionError as RedisConnectionError
            raise RedisConnectionError("result store stopped")

    monkeypatch.setattr(command_dispatch.celery_app, "send_task",
                        lambda *_, **__: ResultStoreUnavailableTask())
    monkeypatch.setattr(command_dispatch, "read_operation",
                        lambda operation_id, **_: {"operation_id": operation_id, "state": "pending"})
    response = command_dispatch.dispatch_command(
        "review.submit", {"card_id": "a"}, idempotency_key="review-result-store-outage",
    )
    assert response.status_code == 202
    assert b'review-result-store-outage' in response.body


def test_postgres_cutover_broker_failure_reports_actionable_error(monkeypatch):
    def unavailable(*_, **__):
        raise BrokerUnavailable("broker stopped")

    monkeypatch.setattr(command_dispatch.celery_app, "send_task", unavailable)
    with pytest.raises(HTTPException) as error:
        command_dispatch.dispatch_command(
            "review.submit", {"card_id": "a"}, idempotency_key="review-a"
        )
    assert error.value.status_code == 503
    assert "same Idempotency-Key" in error.value.detail


def test_postgres_cutover_reused_key_cannot_return_another_commands_receipt(monkeypatch):
    class CompletedTask:
        state = "SUCCESS"

        def get(self, **_):
            return None

    monkeypatch.setattr(command_dispatch.celery_app, "send_task", lambda *_, **__: CompletedTask())

    def conflicting_receipt(*_, **__):
        raise CommandConflict("Operation ID was already used for another request")

    monkeypatch.setattr(command_dispatch, "read_operation", conflicting_receipt)
    with pytest.raises(HTTPException) as error:
        command_dispatch.dispatch_command(
            "studies.create", {"title": "different"}, idempotency_key="reused-key"
        )
    assert error.value.status_code == 409


def test_postgres_cutover_foreground_admission_blocks_background_slice(monkeypatch):
    class AtomicRedis:
        def __init__(self):
            self.lock = threading.Lock()
            self.foreground: set[str] = set()
            self.background: set[str] = set()

        def eval(self, script, _key_count, *_arguments):
            token = _arguments[-2]
            with self.lock:
                if script == redis_admission_gate._REGISTER_FOREGROUND:
                    self.foreground.add(token)
                    return 1
                if self.foreground:
                    return 0
                self.background.add(token)
                return 1

        def zrem(self, key, token):
            with self.lock:
                (self.foreground if key.endswith("foreground") else self.background).discard(token)

    shared_redis = AtomicRedis()
    monkeypatch.setattr(redis_admission_gate, "client", lambda: shared_redis)
    background_started = threading.Event()

    def enter_background():
        with redis_admission_gate.background_lease():
            background_started.set()

    with redis_admission_gate.foreground_lease():
        worker = threading.Thread(target=enter_background)
        worker.start()
        assert not background_started.wait(0.05)
    assert background_started.wait(1)
    worker.join(timeout=1)
    assert not worker.is_alive()


def test_postgres_cutover_priority_retention_locks_bounded_primary_keys(monkeypatch):
    from app.services import priority_retention

    statements: list[str] = []
    deleted: list[tuple[str, int, str]] = []

    class Cursor:
        def __init__(self, row=None, rows=()):
            self.row = row
            self.rows = rows

        def fetchone(self):
            return self.row

        def __iter__(self):
            return iter(self.rows)

    class Database:
        def execute(self, statement, _parameters=()):
            statements.append(statement)
            if "FROM background_tasks" in statement:
                return Cursor(row={"exists": 1})
            if "FROM repertoire_priority_publications" in statement:
                return Cursor(row={"generation": 3})
            if "FROM repertoire_priority_jobs" in statement:
                return Cursor(row={"generation": 4})
            if "SELECT generation,card_id" in statement:
                return Cursor(rows=[{"generation": 2, "card_id": "stale-card"}])
            return Cursor()

        def executemany(self, statement, parameters):
            statements.append(statement)
            deleted.extend(parameters)

    @contextmanager
    def test_connection(*, background):
        assert background
        yield Database()

    monkeypatch.setattr(priority_retention.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(priority_retention, "connection", test_connection)
    monkeypatch.setattr(priority_retention.activity_gate, "wait_for_foreground", lambda: None)
    assert priority_retention.execute_priority_retention_slice({
        "id": "task", "generation": 1, "lease_token": "lease",
        "payload": {"repertoire_id": "repertoire"},
    }) is False
    assert any("FOR UPDATE SKIP LOCKED" in statement for statement in statements)
    assert all("rowid" not in statement for statement in statements)
    assert deleted == [("repertoire", 2, "stale-card")]
