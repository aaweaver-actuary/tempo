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


def test_postgres_cutover_review_route_dispatches_idempotent_command(monkeypatch):
    from fastapi.testclient import TestClient
    from app import main

    dispatched: list[tuple[str, dict, str | None]] = []

    def record_command(name, payload, *, idempotency_key):
        dispatched.append((name, payload, idempotency_key))
        return {"review_id": 19, "persisted": True}

    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(command_dispatch, "dispatch_command", record_command)
    response = TestClient(main.app).post(
        "/api/cards/card-1/review",
        json={"outcome": "correct", "queue_entry_id": 42},
        headers={"Idempotency-Key": "review-card-1-42"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"review_id": 19, "persisted": True}
    assert dispatched[0][0] == "cards.review"
    assert dispatched[0][1]["card_id"] == "card-1"
    assert dispatched[0][1]["review"]["queue_entry_id"] == 42
    assert dispatched[0][2] == "review-card-1-42"


def test_postgres_cutover_background_slice_restarts_only_with_current_lease(monkeypatch):
    from app.services import durable_tasks

    active_task = {"id": "task-1", "generation": 3, "lease_token": "lease-current"}
    events: list[tuple] = []

    class Cursor:
        def __init__(self, *, row=None, rowcount=0):
            self.row = row
            self.rowcount = rowcount

        def fetchone(self):
            return self.row

    class Database:
        def __init__(self):
            self.statements: list[str] = []

        def execute(self, statement, parameters):
            self.statements.append(statement)
            if statement.startswith("SELECT generation"):
                return Cursor(row={"generation": 3, "lease_token": "lease-current", "state": "leased"})
            return Cursor(rowcount=int(parameters[-2:] == (3, "lease-current")))

    monkeypatch.setattr(durable_tasks, "_record_event", lambda *arguments: events.append(arguments))
    database = Database()
    assert durable_tasks.lock_current_slice(database, active_task)
    assert not durable_tasks.lock_current_slice(database, {**active_task, "generation": 2})
    assert durable_tasks.advance_task_slice_in_transaction(
        database, active_task, next_phase="seed", next_payload={"_queue_phase": "seed"},
    )
    assert not durable_tasks.advance_task_slice_in_transaction(
        database, {**active_task, "lease_token": "stale"},
        next_phase="seed", next_payload={"_queue_phase": "seed"},
    )
    assert "attempt_count=0" in database.statements[-1]
    assert len(events) == 1


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


def test_postgres_cutover_card_copy_uses_backend_column_catalog(monkeypatch):
    from app import database as database_module

    with sqlite3.connect(":memory:") as sqlite_database:
        sqlite_database.execute("CREATE TABLE cards(id TEXT PRIMARY KEY, due_date TEXT)")
        assert database_module.card_columns(sqlite_database) == ["id", "due_date"]

    class PgDatabase:
        def execute(self, statement, parameters):
            assert "information_schema.columns" in statement
            assert parameters == ("cards",)
            return [("id",), ("due_date",)]

    monkeypatch.setattr(database_module.postgres_store, "configured", lambda: True)
    assert database_module.card_columns(PgDatabase()) == ["id", "due_date"]


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
                return Cursor(row={"generation": 1, "lease_token": "lease", "state": "leased"})
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
    assert "FOR UPDATE" in statements[0]
    assert any("FOR UPDATE SKIP LOCKED" in statement for statement in statements)
    assert all("rowid" not in statement for statement in statements)
    assert deleted == [("repertoire", 2, "stale-card")]
    assert priority_retention.execute_priority_retention_slice({
        "id": "task", "generation": 1, "lease_token": "expired-lease",
        "payload": {"repertoire_id": "repertoire"},
    }) is False
    assert deleted == [("repertoire", 2, "stale-card")]


def test_postgres_cutover_queue_repertoire_choices_scan_only_active_cards(monkeypatch):
    from app import main

    statements: list[str] = []

    class EmptyCursor:
        def fetchone(self):
            return None

        def fetchall(self):
            return []

    class EmptyDatabase:
        def execute(self, statement, _parameters=()):
            statements.append(statement)
            return EmptyCursor()

    @contextmanager
    def test_read_connection():
        yield EmptyDatabase()

    monkeypatch.setattr(main, "read_connection", test_read_connection)
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    assert main._queue_payload()["cards"] == []
    queue_statement = next(statement for statement in statements if "ranked_repertoires" in statement)
    assert queue_statement.count("FROM active_queue queue_card JOIN cards c") == 2


def test_postgres_cutover_queue_unlock_slice_replays_and_advances_without_skips():
    from app.main import _unlock_eligible_opening_cards

    def populated_database():
        database = sqlite3.connect(":memory:")
        database.row_factory = sqlite3.Row
        database.executescript("""
            CREATE TABLE cards(id TEXT PRIMARY KEY,content_type TEXT,state TEXT,archived INTEGER);
            CREATE TABLE opening_graph_steps(card_id TEXT,parent_card_id TEXT,
                                             repertoire_id TEXT,generation INTEGER);
            CREATE TABLE opening_graph_publications(repertoire_id TEXT,generation INTEGER);
            INSERT INTO opening_graph_publications VALUES('repertoire',1);
        """)
        for card_number in range(129):
            card_id = f"card-{card_number:03}"
            database.execute(
                "INSERT INTO cards VALUES(?,'opening','locked',0)", (card_id,),
            )
            if card_number % 7 == 0:
                database.execute(
                    "INSERT INTO opening_graph_steps VALUES(?,NULL,'repertoire',1)",
                    (card_id,),
                )
        return database

    with populated_database() as complete_database, populated_database() as sliced_database:
        _unlock_eligible_opening_cards(complete_database, "2026-09-27")
        first_cursor = _unlock_eligible_opening_cards(
            sliced_database, "2026-09-27", after_card_id="", batch_size=16,
        )
        assert first_cursor is not None
        # Replaying a committed slice may select a few additional locked cards.
        # The cursor must still advance without skipping any eligible card.
        cursor = _unlock_eligible_opening_cards(
            sliced_database, "2026-09-27", after_card_id="", batch_size=16,
        )
        while cursor is not None:
            cursor = _unlock_eligible_opening_cards(
                sliced_database, "2026-09-27", after_card_id=cursor, batch_size=16,
            )
        complete_states = list(complete_database.execute("SELECT id,state FROM cards ORDER BY id"))
        sliced_states = list(sliced_database.execute("SELECT id,state FROM cards ORDER BY id"))
        assert [tuple(row) for row in sliced_states] == [tuple(row) for row in complete_states]
