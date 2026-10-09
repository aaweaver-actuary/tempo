"""Graph and retention timeouts must preserve work and use bounded durable retries."""

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest
from psycopg.errors import LockNotAvailable, TransactionTimeout

from app import tasks
from app.services import durable_tasks
from app.services.background_runtime import RuntimeMeasurement
from app.services.background_metrics_schema import SCHEMA as BACKGROUND_METRIC_SCHEMA


@pytest.fixture
def timeout_database(monkeypatch):
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    database.executescript(BACKGROUND_METRIC_SCHEMA)
    database.executescript("""
        CREATE TABLE background_tasks(id TEXT PRIMARY KEY,kind TEXT,generation INTEGER,
            state TEXT,phase TEXT,payload_json TEXT,attempt_count INTEGER,max_attempts INTEGER,
            next_attempt_at TEXT,lease_token TEXT,lease_expires_at TEXT,last_error TEXT,
            completed_at TEXT,updated_at TEXT,
            transaction_timeout_count INTEGER NOT NULL DEFAULT 0,
            transaction_timeout_checkpoint TEXT);
        CREATE TABLE background_task_events(id INTEGER PRIMARY KEY,task_id TEXT,
            generation INTEGER,event TEXT,phase TEXT,detail TEXT,created_at TEXT);
    """)
    monkeypatch.setattr(durable_tasks.postgres_store, "configured", lambda: True)
    # This timeout harness models tasks without an application. Real PostgreSQL
    # linked-receipt failure/recovery is covered by the issue80 durability proofs.
    from app.services import prefix_transition_application
    monkeypatch.setattr(prefix_transition_application, "lock_linked_receipts", lambda *_args: None)
    monkeypatch.setattr(prefix_transition_application, "record_linked_failure", lambda *_args: None)
    monkeypatch.setattr(durable_tasks, "submit_background_write",
                        lambda operation, *, label: operation(database))
    monkeypatch.setattr(durable_tasks, "_now", lambda: datetime(2026, 10, 4, tzinfo=timezone.utc))
    monkeypatch.setattr(durable_tasks.random, "random", lambda: 0.0)
    monkeypatch.setattr(tasks, "current_delivery", lambda _task: True)
    monkeypatch.setattr(tasks.activity_gate, "background_job", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(tasks, "measure_handler", lambda *args, **options: nullcontext(RuntimeMeasurement(args[0])))
    monkeypatch.setattr(tasks, "fail_task", durable_tasks.fail_task)
    monkeypatch.setattr(tasks, "defer_task_for_contention", durable_tasks.defer_task_for_contention)
    yield database
    database.close()


def _claimed_task(database, kind, *, attempt_count=1):
    database.execute("DELETE FROM background_tasks")
    database.execute(
        "INSERT INTO background_tasks(id,kind,generation,state,phase,payload_json,"
        "attempt_count,max_attempts,lease_token) VALUES('incident-task',?,3,'leased',"
        "'cleanup','{\"repertoire_id\":\"incident-repertoire\",\"after_card_id\":\"card-07\"}',?,5,'lease')",
        (kind, attempt_count),
    )
    return {"id": "incident-task", "kind": kind, "generation": 3, "lease_token": "lease",
            "phase": "cleanup", "payload": {"repertoire_id": "incident-repertoire", "after_card_id": "card-07"}}


@pytest.mark.parametrize("kind,handler_name", [
    ("opening_graph_rebuild", "execute_postgres_opening_graph_slice"),
    ("priority_retention", "execute_priority_retention_slice"),
])
def test_postgres_graph_retention_timeout_backoff_preserves_checkpoint_and_stops_at_limit(
    timeout_database, monkeypatch, caplog, kind, handler_name,
):
    poll_calls = []
    monkeypatch.setattr(tasks.celery_app, "send_task", lambda *args, **_kwargs: poll_calls.append(args[0]))
    monkeypatch.setattr(tasks, handler_name,
                        lambda _task: (_ for _ in ()).throw(TransactionTimeout("transaction budget expired")))
    for attempt_count in range(1, 6):
        claimed = _claimed_task(timeout_database, kind, attempt_count=attempt_count)
        result = tasks.execute_background_slice.run(claimed)
        saved = timeout_database.execute("SELECT * FROM background_tasks").fetchone()
        assert result is (attempt_count < 5)
        assert saved["attempt_count"] == attempt_count
        assert saved["phase"] == "cleanup"
        assert saved["payload_json"] == '{"repertoire_id":"incident-repertoire","after_card_id":"card-07"}'
        assert saved["lease_token"] is None and saved["lease_expires_at"] is None
        assert saved["last_error"] == "transaction budget expired"
        assert saved["state"] == ("retrying" if attempt_count < 5 else "failed")
        expected_delay = timedelta(seconds=2 ** (attempt_count - 1))
        assert datetime.fromisoformat(saved["next_attempt_at"]) == durable_tasks._now() + expected_delay
    assert len(poll_calls) == 4
    assert "outcome=failed" in caplog.text and "sqlstate=25P04" in caplog.text
    assert timeout_database.execute("SELECT COUNT(*) FROM background_task_events WHERE event='retrying'").fetchone()[0] == 4
    assert timeout_database.execute("SELECT COUNT(*) FROM background_task_events WHERE event='failed'").fetchone()[0] == 1


@pytest.mark.parametrize("kind,handler_name,error_type", [
    ("opening_graph_rebuild", "execute_postgres_opening_graph_slice", LockNotAvailable),
    ("priority_retention", "execute_priority_retention_slice", LockNotAvailable),
])
def test_postgres_other_timeouts_and_target_lock_contention_keep_existing_yield(
    timeout_database, monkeypatch, kind, handler_name, error_type,
):
    claimed = _claimed_task(timeout_database, kind)
    monkeypatch.setattr(tasks.celery_app, "send_task", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tasks, handler_name,
                        lambda _task: (_ for _ in ()).throw(error_type("busy")))
    assert tasks.execute_background_slice.run(claimed) is True
    saved = timeout_database.execute("SELECT * FROM background_tasks").fetchone()
    assert saved["attempt_count"] == 0 and saved["state"] == "retrying"
    assert saved["phase"] == "cleanup" and saved["last_error"] is None
    assert datetime.fromisoformat(saved["next_attempt_at"]) == durable_tasks._now() + timedelta(milliseconds=250)


def test_daily_queue_transaction_deadlines_preserve_checkpoint_and_reach_cooldown(
    timeout_database, monkeypatch,
):
    claimed = _claimed_task(timeout_database, "daily_queue")
    monkeypatch.setattr(tasks.celery_app, "send_task", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice",
                        lambda _task: (_ for _ in ()).throw(TransactionTimeout("unlock query exceeded budget")))
    for timeout_number, expected_delay in enumerate((1, 2, 4, 8, 16, 32, 60, 60), start=1):
        timeout_database.execute("UPDATE background_tasks SET state='leased',lease_token='lease',attempt_count=1")
        assert tasks.execute_background_slice.run(claimed) is True
        saved = timeout_database.execute("SELECT * FROM background_tasks").fetchone()
        assert saved["transaction_timeout_count"] == timeout_number
        assert saved["attempt_count"] == 0 and saved["state"] == "retrying"
        assert saved["phase"] == "cleanup" and 'card-07' in saved["payload_json"]
        assert saved["last_error"] == "unlock query exceeded budget"
        assert datetime.fromisoformat(saved["next_attempt_at"]) == durable_tasks._now() + timedelta(seconds=expected_delay)
    # A genuinely advancing checkpoint begins a new timeout episode.
    timeout_database.execute("UPDATE background_tasks SET state='leased',lease_token='lease',attempt_count=1,payload_json='{\"after_card_id\":\"card-08\"}'")
    assert tasks.execute_background_slice.run(claimed) is True
    assert timeout_database.execute("SELECT transaction_timeout_count FROM background_tasks").fetchone()[0] == 1


def test_daily_queue_timeout_cannot_change_a_replacement_generation(timeout_database, monkeypatch):
    claimed = _claimed_task(timeout_database, "daily_queue")
    timeout_database.execute("UPDATE background_tasks SET generation=4,lease_token='new-lease'")
    monkeypatch.setattr(tasks.celery_app, "send_task", lambda *_args, **_kwargs: pytest.fail("No stale wake"))
    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice",
                        lambda _task: (_ for _ in ()).throw(TransactionTimeout("expired")))
    assert tasks.execute_background_slice.run(claimed) is False
    saved = timeout_database.execute("SELECT * FROM background_tasks").fetchone()
    assert saved["generation"] == 4 and saved["lease_token"] == "new-lease"
    assert saved["transaction_timeout_count"] == 0 and saved["last_error"] is None


def test_postgres_graph_timeout_superseded_generation_does_not_retry_or_fail_replacement(
    timeout_database, monkeypatch,
):
    claimed = _claimed_task(timeout_database, "opening_graph_rebuild")
    timeout_database.execute("UPDATE background_tasks SET generation=4,lease_token='replacement'")
    monkeypatch.setattr(tasks.celery_app, "send_task", lambda *_args, **_kwargs: pytest.fail("No superseded wakeup"))
    monkeypatch.setattr(tasks, "execute_postgres_opening_graph_slice",
                        lambda _task: (_ for _ in ()).throw(TransactionTimeout("expired")))
    assert tasks.execute_background_slice.run(claimed) is False
    saved = timeout_database.execute("SELECT * FROM background_tasks").fetchone()
    assert saved["generation"] == 4 and saved["lease_token"] == "replacement"
    assert saved["state"] == "leased" and saved["last_error"] is None
