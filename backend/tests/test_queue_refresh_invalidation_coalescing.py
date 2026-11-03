"""Queue refresh requests must preserve active work and durable freshness.

The request and publication epochs proposed by the queue owner are a contract,
not evidence that the storage migration or native PostgreSQL fencing exists.
"""

import json

import pytest

from app.services import durable_tasks, queue_application
from tempo_legacy_database import connections as database

DAY = "2026-10-07"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "queue-coalescing.sqlite")
    database.initialize()
    monkeypatch.setattr(durable_tasks, "wake_queue_refresh", lambda **kwargs: None)
    return database


def request(store, day=DAY, preserve=None):
    with store.connection(background=True) as connection:
        return queue_application.request_queue_refresh_in_transaction(
            connection, day, preserve_through_entry_id=preserve,
        )


def task_and_projection(store, day=DAY):
    with store.read_connection() as connection:
        task = dict(connection.execute(
            "SELECT * FROM background_tasks WHERE kind='daily_queue' AND deduplication_key='current'"
        ).fetchone())
        projection = dict(connection.execute(
            "SELECT * FROM queue_projections WHERE queue_date=?", (day,)
        ).fetchone())
    return task, projection


@pytest.mark.parametrize("state", ["queued", "leased", "retrying"])
def test_queue_refresh_active_request_retains_generation_lease_and_checkpoint(store, state):
    initial = request(store, preserve=42)
    payload = {"queue_date": DAY, "preserve_through_entry_id": 42,
               "_queue_phase": "admit_due", "cursor": 19, "_refresh_cycle_epoch": 1}
    with store.connection(background=True) as connection:
        connection.execute(
            "UPDATE background_tasks SET state=?, phase=?, payload_json=?, lease_token=?, "
            "lease_expires_at=?, transaction_timeout_count=?, transaction_timeout_checkpoint=? WHERE id=?",
            (state, "admit_due", json.dumps(payload), "lease-1" if state == "leased" else None,
             "2030-01-01T00:00:00+00:00" if state == "leased" else None, 3, "admit_due:19", initial["id"]),
        )
    before, _ = task_and_projection(store)
    request(store)
    after, projection = task_and_projection(store)
    for field in ("id", "generation", "state", "phase", "lease_token", "lease_expires_at",
                  "transaction_timeout_count", "transaction_timeout_checkpoint"):
        assert after[field] == before[field], field
    assert json.loads(after["payload_json"]) == payload
    assert projection["state"] == "refreshing" and projection["refresh_pending"] == 1
    assert projection["requested_refresh_epoch"] > payload["_refresh_cycle_epoch"]
    assert projection["published_refresh_epoch"] < projection["requested_refresh_epoch"]


def test_queue_refresh_twenty_committed_requests_coalesce_and_record_each_invalidation(store):
    initial = request(store)
    for _ in range(20):
        request(store)
    task, projection = task_and_projection(store)
    assert task["id"] == initial["id"]
    assert task["generation"] == initial["generation"]
    assert projection["requested_refresh_epoch"] == 21
    assert projection["published_refresh_epoch"] < projection["requested_refresh_epoch"]


def test_queue_refresh_rolled_back_request_does_not_change_task_or_projection(store):
    request(store)
    before = task_and_projection(store)
    with pytest.raises(RuntimeError, match="abort review"):
        with store.connection(background=True) as connection:
            queue_application.request_queue_refresh_in_transaction(connection, DAY)
            raise RuntimeError("abort review")
    assert task_and_projection(store) == before


@pytest.mark.parametrize("state", ["complete", "failed"])
def test_queue_refresh_terminal_task_starts_new_generation(store, state):
    initial = request(store)
    with store.connection(background=True) as connection:
        connection.execute("UPDATE background_tasks SET state=? WHERE id=?", (state, initial["id"]))
    request(store)
    task, projection = task_and_projection(store)
    assert task["generation"] > initial["generation"]
    assert task["state"] == "queued"
    assert projection["state"] == "refreshing" and projection["refresh_pending"] == 1


def test_queue_refresh_new_day_replaces_singleton_without_marking_old_day_ready(store):
    initial = request(store)
    next_day = "2026-10-08"
    request(store, next_day)
    task, projection = task_and_projection(store, next_day)
    assert task["generation"] > initial["generation"]
    assert json.loads(task["payload_json"])["queue_date"] == next_day
    assert projection["state"] == "refreshing" and projection["refresh_pending"] == 1
    _, old = task_and_projection(store)
    assert old["state"] != "ready"
