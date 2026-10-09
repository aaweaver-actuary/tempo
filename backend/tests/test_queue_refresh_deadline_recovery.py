"""Issue 135: queue deadline episodes and error status follow durable progress."""

from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest
from psycopg.errors import TransactionTimeout

from app import database
from app import activity_commands, queue_commands
from app.services import durable_tasks


QUEUE_DATE = "2026-10-07"


class NativeSqlite:
    """Execute the portable PostgreSQL command statements on this small store."""

    def __init__(self, connection):
        self.connection = connection

    def execute(self, statement, parameters=()):
        return self.connection.execute(statement.replace(" FOR UPDATE", ""), parameters)

    def execute_native(self, statement, parameters=()):
        return self.execute(statement.replace("%s", "?"), parameters)


@pytest.fixture
def queue_refresh_store(tmp_path, monkeypatch):
    database_path = tmp_path / "queue-refresh.sqlite"
    monkeypatch.setattr(database, "DB_PATH", database_path)
    database.initialize()
    observed_clock = [datetime(2026, 10, 7, 8, tzinfo=timezone.utc)]
    monkeypatch.setattr(durable_tasks, "_now", lambda: observed_clock[0])
    monkeypatch.setattr(durable_tasks.random, "random", lambda: 0.0)

    def write(operation, *, label):
        with database.connection(background=True) as connection:
            return operation(connection)

    monkeypatch.setattr(durable_tasks, "submit_background_write", write)
    monkeypatch.setattr(durable_tasks, "submit_foreground_write", write)
    with database.connection(background=True) as connection:
        connection.execute(
            "INSERT INTO queue_projections(queue_date,state,generation,refresh_pending) "
            "VALUES(?,'refreshing',0,1)", (QUEUE_DATE,),
        )
    return observed_clock


def _queue_task():
    return durable_tasks.enqueue_task(
        "daily_queue", "current", {"queue_date": QUEUE_DATE}, priority=10,
        foreground=False,
    )


def _saved_task(task_id):
    with database.read_connection() as connection:
        return dict(connection.execute(
            "SELECT * FROM background_tasks WHERE id=?", (task_id,),
        ).fetchone())


def _projection():
    with database.read_connection() as connection:
        return dict(connection.execute(
            "SELECT * FROM queue_projections WHERE queue_date=?", (QUEUE_DATE,),
        ).fetchone())


def _deadline(claimed_task):
    return durable_tasks.defer_task_for_transaction_timeout(
        claimed_task, TransactionTimeout("Daily queue database deadline exceeded"),
    )


@pytest.mark.parametrize("previous_state", ["retrying", "complete"])
def test_issue135_new_queue_generation_does_not_inherit_deadline_cooldown(
    queue_refresh_store, previous_state,
):
    observed_clock = queue_refresh_store
    for _generation_number in range(7):
        queued = _queue_task()
        claimed = durable_tasks.claim_task("daily_queue")
        assert _deadline(claimed)
        saved = _saved_task(queued["id"])
        assert saved["transaction_timeout_count"] == 1
        assert datetime.fromisoformat(saved["next_attempt_at"]) == observed_clock[0] + timedelta(seconds=1)
        if previous_state == "complete":
            observed_clock[0] += timedelta(seconds=1)
            resumed = durable_tasks.claim_task("daily_queue")
            with database.connection(background=True) as connection:
                assert durable_tasks.complete_task_slice_in_transaction(connection, resumed)


@pytest.mark.parametrize("transition", ["advance", "complete_slice", "complete"])
def test_issue135_queue_progress_resets_deadline_episode(queue_refresh_store, transition):
    observed_clock = queue_refresh_store
    queued = _queue_task()
    claimed = durable_tasks.claim_task("daily_queue")
    assert _deadline(claimed)
    observed_clock[0] += timedelta(seconds=1)
    resumed = durable_tasks.claim_task("daily_queue")
    if transition == "complete":
        assert durable_tasks.complete_task(resumed["id"], resumed["generation"], resumed["lease_token"], kind="daily_queue")
    else:
        with database.connection(background=True) as connection:
            if transition == "advance":
                assert durable_tasks.advance_task_slice_in_transaction(
                    connection, resumed, next_phase="admit_due",
                    next_payload={"queue_date": QUEUE_DATE, "_queue_phase": "admit_due"},
                )
            else:
                assert durable_tasks.complete_task_slice_in_transaction(connection, resumed)
    saved = _saved_task(queued["id"])
    assert saved["transaction_timeout_count"] == 0
    assert saved["transaction_timeout_checkpoint"] is None
    assert saved["last_error"] is None
    assert _projection()["last_error"] is None


def test_issue135_queue_retry_error_is_visible_and_generation_fenced(queue_refresh_store):
    queued = _queue_task()
    original_delivery = durable_tasks.claim_task("daily_queue")
    assert _deadline(original_delivery)
    projection = _projection()
    assert projection["state"] == "refreshing" and projection["refresh_pending"] == 1
    assert projection["last_error"] == "Daily queue database deadline exceeded"
    assert _saved_task(queued["id"])["state"] == "retrying"
    replacement = _queue_task()
    with database.connection(background=True) as connection:
        connection.execute("UPDATE queue_projections SET last_error=NULL WHERE queue_date=?", (QUEUE_DATE,))
    assert not _deadline(original_delivery)
    with database.connection(background=True) as connection:
        assert not durable_tasks.advance_task_slice_in_transaction(
            connection, original_delivery, next_phase="stale",
            next_payload=original_delivery["payload"],
        )
        assert not durable_tasks.complete_task_slice_in_transaction(connection, original_delivery)
    assert durable_tasks.fail_task(original_delivery["id"], original_delivery["generation"], original_delivery["lease_token"], RuntimeError("stale error"))["state"] == "superseded"
    assert _saved_task(queued["id"])["generation"] == replacement["generation"]
    assert _projection()["last_error"] is None


def test_issue135_unchanged_checkpoint_backoff_survives_restart_and_stale_replay(queue_refresh_store):
    observed_clock = queue_refresh_store
    queued = _queue_task()
    for timeout_number, delay_seconds in enumerate((1, 2, 4, 8, 16, 32, 60, 60), start=1):
        claimed = durable_tasks.claim_task("daily_queue")
        assert _deadline(claimed)
        saved = _saved_task(queued["id"])
        assert saved["transaction_timeout_count"] == timeout_number
        assert saved["attempt_count"] == 0
        assert datetime.fromisoformat(saved["next_attempt_at"]) == observed_clock[0] + timedelta(seconds=delay_seconds)
        # Reopen the store and replay an acknowledged delivery without progress.
        assert _saved_task(queued["id"])["transaction_timeout_count"] == timeout_number
        assert not _deadline(claimed)
        assert _saved_task(queued["id"])["transaction_timeout_count"] == timeout_number
        observed_clock[0] += timedelta(seconds=delay_seconds)
    restarted_delivery = durable_tasks.claim_task("daily_queue")
    observed_clock[0] += timedelta(seconds=61)
    reclaimed_delivery = durable_tasks.claim_task("daily_queue")
    assert reclaimed_delivery["lease_token"] != restarted_delivery["lease_token"]
    assert _saved_task(queued["id"])["transaction_timeout_count"] == 8
    assert not _deadline(restarted_delivery)
    assert _deadline(reclaimed_delivery)
    assert _saved_task(queued["id"])["transaction_timeout_count"] == 9


@pytest.mark.parametrize("retry_path", ["compatibility", "postgres_command"])
def test_issue135_terminal_queue_failure_and_explicit_retry_publish_recoverable_status(queue_refresh_store, retry_path):
    queued = _queue_task()
    claimed = durable_tasks.claim_task("daily_queue")
    assert _deadline(claimed)
    queue_refresh_store[0] += timedelta(seconds=1)
    resumed = durable_tasks.claim_task("daily_queue")
    with database.connection(background=True) as connection:
        connection.execute("UPDATE background_tasks SET attempt_count=max_attempts WHERE id=?", (queued["id"],))
    assert durable_tasks.fail_task(resumed["id"], resumed["generation"], resumed["lease_token"], RuntimeError("Queue preparation needs repair"))["state"] == "failed"
    projection = _projection()
    assert (projection["state"], projection["refresh_pending"], projection["last_error"]) == ("failed", 0, "Queue preparation needs repair")
    if retry_path == "compatibility":
        assert durable_tasks.retry_task(queued["id"])["state"] == "queued"
    else:
        with database.connection(background=True) as connection:
            assert activity_commands.retry_failed_task(NativeSqlite(connection), {"task_id": queued["id"]})["state"] == "queued"
    saved = _saved_task(queued["id"])
    assert saved["transaction_timeout_count"] == 0 and saved["transaction_timeout_checkpoint"] is None
    projection = _projection()
    assert (projection["state"], projection["refresh_pending"], projection["last_error"]) == ("refreshing", 1, None)
    assert json.loads(saved["payload_json"]) == {"queue_date": QUEUE_DATE}


def test_issue135_queue_failure_and_projection_error_roll_back_together(queue_refresh_store, monkeypatch):
    queued = _queue_task()
    claimed = durable_tasks.claim_task("daily_queue")
    original_status_update = durable_tasks.update_queue_refresh_status_in_transaction

    def interrupted_projection_update(*args, **kwargs):
        original_status_update(*args, **kwargs)
        raise RuntimeError("controlled failure after projection update")

    monkeypatch.setattr(durable_tasks, "update_queue_refresh_status_in_transaction", interrupted_projection_update)
    with pytest.raises(RuntimeError, match="controlled failure"):
        _deadline(claimed)
    saved = _saved_task(queued["id"])
    assert saved["state"] == "leased" and saved["lease_token"] == claimed["lease_token"]
    assert saved["transaction_timeout_count"] == 0 and saved["last_error"] is None
    assert _projection()["last_error"] is None


def test_issue135_ordinary_queue_retry_reports_error_without_false_readiness(queue_refresh_store):
    queued = _queue_task()
    claimed = durable_tasks.claim_task("daily_queue")
    result = durable_tasks.fail_task(claimed["id"], claimed["generation"], claimed["lease_token"], RuntimeError("Retryable queue failure"))
    assert result["state"] == "retrying"
    assert result["next_attempt_at"] == (queue_refresh_store[0] + timedelta(seconds=1)).isoformat()
    assert _saved_task(queued["id"])["last_error"] == "Retryable queue failure"
    projection = _projection()
    assert (projection["state"], projection["refresh_pending"], projection["last_error"]) == ("refreshing", 1, "Retryable queue failure")


def test_issue135_pre_upgrade_deadline_checkpoint_resumes_without_losing_backoff(queue_refresh_store):
    queued = _queue_task()
    claimed = durable_tasks.claim_task("daily_queue")
    legacy_checkpoint = json.dumps([claimed["phase"], claimed["payload"]], sort_keys=True)
    with database.connection(background=True) as connection:
        connection.execute("UPDATE background_tasks SET transaction_timeout_count=5,transaction_timeout_checkpoint=? WHERE id=?", (legacy_checkpoint, queued["id"]))
    assert _deadline(claimed)
    saved = _saved_task(queued["id"])
    assert saved["transaction_timeout_count"] == 6
    assert datetime.fromisoformat(saved["next_attempt_at"]) == queue_refresh_store[0] + timedelta(seconds=32)
    assert json.loads(saved["transaction_timeout_checkpoint"])[0] == claimed["generation"]


def test_issue135_periodic_ensure_preserves_current_queue_retry_error(queue_refresh_store):
    queued = _queue_task()
    claimed = durable_tasks.claim_task("daily_queue")
    assert _deadline(claimed)
    before_ensure = _saved_task(queued["id"])
    with database.connection(background=True) as connection:
        ensured = queue_commands.ensure_current_queue(NativeSqlite(connection), {"queue_date": QUEUE_DATE})
    assert ensured["task_id"] == queued["id"] and ensured["refresh_pending"] is True
    assert _saved_task(queued["id"]) == before_ensure
    assert _projection()["last_error"] == "Daily queue database deadline exceeded"
