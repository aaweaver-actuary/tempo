"""Study queue work must claim its lease only at execution capacity."""

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from threading import Event, Thread

import pytest
from kombu.exceptions import OperationalError as BrokerUnavailable

from app import database, tasks
from app.services import durable_tasks


@pytest.fixture
def dispatch_store(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "dispatch.sqlite")
    database.initialize()
    observed_clock = [datetime(2026, 10, 7, 8, tzinfo=timezone.utc)]
    monkeypatch.setattr(durable_tasks, "_now", lambda: observed_clock[0])
    def write(operation, *, label):
        with database.connection(background=True) as connection:
            return operation(connection)
    monkeypatch.setattr(durable_tasks, "submit_background_write", write)
    monkeypatch.setattr(tasks, "measure_handler", lambda *args: nullcontext())
    wakes = []
    monkeypatch.setattr(tasks.celery_app, "send_task", lambda *args, **kwargs: wakes.append((args, kwargs)))
    return observed_clock, wakes


def enqueue_daily():
    return durable_tasks.enqueue_task("daily_queue", "current", {"queue_date": "2026-10-07"}, priority=10, foreground=False)


def task_row(identifier):
    with database.read_connection() as connection:
        return dict(connection.execute("SELECT * FROM background_tasks WHERE id=?", (identifier,)).fetchone())


def test_background_lease_starts_when_slice_execution_begins(dispatch_store, monkeypatch):
    observed_clock, wakes = dispatch_store
    queued = enqueue_daily()
    observed_clock[0] += timedelta(seconds=120)
    assert task_row(queued["id"])["lease_token"] is None
    executed = []
    def execute_one_slice(claimed):
        executed.append(claimed)
        assert datetime.fromisoformat(claimed["lease_expires_at"]) == observed_clock[0] + timedelta(seconds=60)
        with database.connection(background=True) as connection:
            durable_tasks.complete_task_slice_in_transaction(connection, claimed)
        return False
    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", execute_one_slice)
    tasks.poll_background_tasks.run()
    assert len(executed) == 1
    assert task_row(queued["id"])["state"] == "complete"
    assert wakes == []


def test_background_legacy_deliveries_preserve_restart_and_replay(dispatch_store, monkeypatch):
    observed_clock, wakes = dispatch_store
    queued = enqueue_daily()
    crashed_delivery = durable_tasks.claim_task("daily_queue")
    observed_clock[0] += timedelta(seconds=61)
    executed = []
    def complete_one(claimed):
        executed.append(claimed["lease_token"])
        with database.connection(background=True) as connection:
            durable_tasks.complete_task_slice_in_transaction(connection, claimed)
        return False
    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", complete_one)
    tasks.poll_background_tasks.run()
    assert task_row(queued["id"])["state"] == "complete"
    assert tasks.execute_background_slice.run(crashed_delivery) is False
    tasks.poll_background_tasks.run()
    assert len(executed) == 1 and executed[0] != crashed_delivery["lease_token"]
    assert not any(args[0] == "app.tasks.execute_background_slice" for args, _ in wakes)


def test_background_continuation_broker_failure_retains_committed_slice(dispatch_store, monkeypatch):
    observed_clock, _wakes = dispatch_store
    queued = enqueue_daily()
    executed_phases = []
    def execute_phase(claimed):
        phase = claimed["payload"].get("_queue_phase", "unlock_opening")
        executed_phases.append(phase)
        with database.connection(background=True) as connection:
            if phase == "unlock_opening":
                durable_tasks.advance_task_slice_in_transaction(connection, claimed, next_phase="admit_due",
                    next_payload={"queue_date": "2026-10-07", "_queue_phase": "admit_due"})
                return True
            durable_tasks.complete_task_slice_in_transaction(connection, claimed)
            return False
    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", execute_phase)
    def unavailable(*args, **kwargs):
        raise BrokerUnavailable("controlled missing continuation wake")
    monkeypatch.setattr(tasks.celery_app, "send_task", unavailable)
    tasks.poll_background_tasks.run()
    assert task_row(queued["id"])["state"] == "queued"
    observed_clock[0] += timedelta(seconds=1)
    tasks.poll_background_tasks.run()
    assert executed_phases == ["unlock_opening", "admit_due"]
    assert task_row(queued["id"])["state"] == "complete"


def test_background_current_legacy_delivery_commits_once_before_duplicate_wake(dispatch_store, monkeypatch):
    enqueue_daily()
    legacy_delivery = durable_tasks.claim_task("daily_queue")
    executions = []
    def complete_once(claimed):
        executions.append(claimed["id"])
        with database.connection(background=True) as connection:
            assert durable_tasks.complete_task_slice_in_transaction(connection, claimed)
        return False
    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", complete_once)
    tasks.execute_background_slice.run(legacy_delivery)
    # Commit-before-ack replay and duplicate wakes cannot publish again.
    tasks.execute_background_slice.run(legacy_delivery)
    tasks.poll_background_tasks.run()
    assert executions == [legacy_delivery["id"]]


def test_background_crash_during_slice_rolls_back_then_recovers_after_lease(dispatch_store, monkeypatch):
    observed_clock, _wakes = dispatch_store
    queued = enqueue_daily()
    class WorkerLost(BaseException):
        pass
    def interrupted(claimed):
        with database.connection(background=True) as connection:
            connection.execute("UPDATE background_tasks SET phase='uncommitted' WHERE id=?", (claimed["id"],))
            raise WorkerLost()
    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", interrupted)
    with pytest.raises(WorkerLost):
        tasks.poll_background_tasks.run()
    assert task_row(queued["id"])["phase"] != "uncommitted"
    assert task_row(queued["id"])["state"] == "leased"
    observed_clock[0] += timedelta(seconds=61)
    def recovered(claimed):
        with database.connection(background=True) as connection:
            assert durable_tasks.complete_task_slice_in_transaction(connection, claimed)
        return False
    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", recovered)
    tasks.poll_background_tasks.run()
    assert task_row(queued["id"])["state"] == "complete"


def test_background_execution_preserves_pause_priority_promotion_and_delayed_eligibility(dispatch_store, monkeypatch):
    observed_clock, _wakes = dispatch_store
    queued_tasks = [durable_tasks.enqueue_task("daily_queue", identifier, {}, priority=priority,
        delay_seconds=delay, foreground=False) for identifier, priority, delay in
        (("paused", 1, 0), ("delayed", 1, 120), ("normal", 100, 0), ("promoted", 100, 0), ("study", 10, 0))]
    with database.connection(background=True) as connection:
        for queued, paused, promoted in ((queued_tasks[0], 1, 0), (queued_tasks[3], 0, 1)):
            connection.execute("INSERT INTO background_activity(source,work_id,paused,promoted,updated_at) VALUES('durable',?,?,?,?)",
                (queued["id"], paused, promoted, observed_clock[0].isoformat()))
    executed = []
    def complete_one(claimed):
        executed.append(claimed["deduplication_key"])
        with database.connection(background=True) as connection:
            durable_tasks.complete_task_slice_in_transaction(connection, claimed)
        return False
    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", complete_one)
    for _ in range(4):
        tasks.poll_background_tasks.run()
    assert executed == ["study", "promoted", "normal"]
    observed_clock[0] += timedelta(seconds=120)
    tasks.poll_background_tasks.run()
    assert executed[-1] == "delayed"
    assert task_row(queued_tasks[0]["id"])["state"] == "queued"


def test_background_execution_capacity_yields_to_foreground_without_leasing(dispatch_store, monkeypatch):
    queued = enqueue_daily()
    claim_started = Event()
    worker_finished = Event()
    worker_errors = []
    original_claim = tasks.claim_task
    def observed_claim(**kwargs):
        claim_started.set()
        return original_claim(**kwargs)
    def complete_one(claimed):
        with database.connection(background=True) as connection:
            durable_tasks.complete_task_slice_in_transaction(connection, claimed)
        return False
    def run_worker():
        try:
            tasks.poll_background_tasks.run()
        except BaseException as error:
            worker_errors.append(error)
        finally:
            worker_finished.set()
    monkeypatch.setattr(tasks, "claim_task", observed_claim)
    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", complete_one)
    worker = Thread(target=run_worker)
    with tasks.activity_gate.foreground():
        worker.start()
        assert claim_started.wait(2)
        assert not worker_finished.wait(0.05)
        assert task_row(queued["id"])["lease_token"] is None
    worker.join(2)
    assert not worker.is_alive() and worker_errors == []
    assert task_row(queued["id"])["state"] == "complete"


def test_background_congested_wakes_complete_without_broker_lease_expiries(dispatch_store, monkeypatch):
    observed_clock, wakes = dispatch_store
    queued_tasks = [durable_tasks.enqueue_task("daily_queue", f"delayed-wake-{number}", {},
        foreground=False) for number in range(5)]
    executions = []
    def complete_one(claimed):
        executions.append(claimed["id"])
        with database.connection(background=True) as connection:
            durable_tasks.complete_task_slice_in_transaction(connection, claimed)
        return False
    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", complete_one)
    for _ in queued_tasks:
        # Broker delay belongs to an unleased wake, even beyond the old lease.
        observed_clock[0] += timedelta(seconds=120)
        tasks.poll_background_tasks.run()
    with database.read_connection() as connection:
        counters = connection.execute("SELECT COALESCE(SUM(lease_expiries),0),COALESCE(SUM(stale_deliveries),0),COALESCE(SUM(completed_generations),0) FROM background_metric_buckets WHERE kind='daily_queue'").fetchone()
    assert tuple(counters) == (0, 0, 5)
    assert len(executions) == 5 and wakes == []
