"""Study queue work must claim its lease only at execution capacity."""

from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from threading import Event, Thread

import pytest
from kombu.exceptions import OperationalError as BrokerUnavailable

from app import database, tasks
from app.services import durable_tasks


def test_issue107_browser_fixture_uses_application_day_without_host_clock(monkeypatch):
    from scripts import check_postgres_daily_study_dispatch as proof
    statements, queue_dates = [], []
    class FixtureDatabase:
        def execute(self, statement, parameters=()):
            statements.append((statement, parameters))
        def commit(self):
            pass
    fixture_database = FixtureDatabase()
    monkeypatch.setattr(proof.psycopg, "connect", lambda *_: nullcontext(fixture_database))
    monkeypatch.setattr(proof.postgres_store, "connection", lambda: nullcontext(fixture_database))
    monkeypatch.setattr(proof, "request_queue_refresh_in_transaction", lambda _db, day: queue_dates.append(day))
    class HostClockForbidden:
        fromisoformat = staticmethod(date.fromisoformat)

        @staticmethod
        def today():
            pytest.fail("The browser fixture must use its application's supplied day")
    monkeypatch.setattr(proof, "date", HostClockForbidden)
    proof.seed("daily-study-proof-fixture", request_refresh=True, queue_date="2026-10-08")
    assert queue_dates == ["2026-10-08"]
    locked_inserts = [parameters for statement, parameters in statements if "generate_series" in statement and "INSERT INTO cards" in statement]
    assert locked_inserts and all(parameters[3] == "2026-10-08" for parameters in locked_inserts)


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
    executed = []
    def complete_one(claimed):
        executed.append(claimed['id'])
        with database.connection(background=True) as connection:
            durable_tasks.complete_task_slice_in_transaction(connection, claimed)
        return False
    monkeypatch.setattr(tasks, 'execute_postgres_queue_refresh_slice', complete_one)
    with tasks.activity_gate.foreground():
        for _ in range(100):
            assert tasks.poll_background_tasks.run() is False
        assert task_row(queued['id'])['lease_token'] is None
        assert task_row(queued['id'])['attempt_count'] == 0
        assert dispatch_store[1] == []
    tasks.poll_background_tasks.run()
    assert executed == [queued['id']]
    assert task_row(queued['id'])['state'] == 'complete'


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


def test_denied_background_admission_releases_worker_without_claim_or_failure(dispatch_store, monkeypatch):
    queued = enqueue_daily()
    monkeypatch.setattr(tasks, 'claim_task', lambda **kwargs: pytest.fail('denied work must remain unleased'))
    finished = Event()
    errors = []
    def poll():
        try:
            assert tasks.poll_background_tasks.run() is False
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()
    with tasks.activity_gate.foreground():
        worker = Thread(target=poll)
        worker.start()
        assert finished.wait(1), 'foreground denial occupied the worker'
        row = task_row(queued['id'])
        assert row['state'] == 'queued' and row['attempt_count'] == 0
        assert row['lease_token'] is None
    worker.join(1)
    assert not worker.is_alive() and errors == []


def test_foreground_arriving_after_claim_preserves_checkpoint_and_resumes(dispatch_store, monkeypatch):
    clock, wakes = dispatch_store
    queued = enqueue_daily()
    with database.connection(background=True) as connection:
        connection.execute("UPDATE background_tasks SET phase='admit_due',payload_json=? WHERE id=?",
                           ('{"_queue_phase":"admit_due","after_card_id":"saved"}', queued['id']))
    executions = []
    def raced_slice(claimed):
        with tasks.activity_gate.foreground():
            with database.connection(background=True) as connection:
                pytest.fail('database admission must yield after foreground arrives')
    monkeypatch.setattr(tasks, 'execute_postgres_queue_refresh_slice', raced_slice)
    assert tasks.poll_background_tasks.run() is False
    saved = task_row(queued['id'])
    assert saved['state'] == 'retrying' and saved['attempt_count'] == 0
    assert saved['phase'] == 'admit_due' and 'saved' in saved['payload_json']
    assert saved['lease_token'] is None and saved['last_error'] is None and wakes == []
    clock[0] += timedelta(seconds=1)
    def complete(claimed):
        executions.append(claimed['payload']['after_card_id'])
        with database.connection(background=True) as connection:
            assert durable_tasks.complete_task_slice_in_transaction(connection, claimed)
        return False
    monkeypatch.setattr(tasks, 'execute_postgres_queue_refresh_slice', complete)
    tasks.poll_background_tasks.run()
    tasks.poll_background_tasks.run()
    assert executions == ['saved'] and task_row(queued['id'])['state'] == 'complete'


def test_foreground_deferral_cannot_overwrite_new_generation(dispatch_store):
    queued = enqueue_daily()
    stale_claim = durable_tasks.claim_task('daily_queue')
    replacement = enqueue_daily()
    with tasks.activity_gate.foreground():
        assert durable_tasks.defer_task_for_foreground(stale_claim) is False
    current = task_row(queued['id'])
    assert current['generation'] == replacement['generation'] and current['state'] == 'queued'
    assert current['attempt_count'] == 0 and current['lease_token'] is None


def test_accepted_background_receipt_remains_prompt_during_foreground(dispatch_store, monkeypatch):
    observed = []
    def record(*args, **kwargs):
        with database.connection(background=True):
            observed.append('receipt')
        return True, {}, 'owned-attempt', 1
    def publish(*args, **kwargs):
        with database.connection(background=True):
            observed.append('publication')
        return {'accepted': True}
    monkeypatch.setattr(tasks, 'record_operation_attempt', record)
    monkeypatch.setattr(tasks, 'execute_command', publish)
    with tasks.activity_gate.foreground():
        assert tasks.execute_background_command.run('report', 'games.analysis.position.report', {}) == {'accepted': True}
    assert observed == ['receipt', 'publication']


def test_discretionary_background_command_retains_receipt_without_consuming_failure(dispatch_store, monkeypatch):
    deferred = []
    monkeypatch.setattr(tasks, 'record_operation_attempt', lambda *args, **kwargs: (True, {}, 'owned', 1))
    monkeypatch.setattr(tasks, 'execute_command', lambda *args, **kwargs: pytest.fail('denied calculation'))
    monkeypatch.setattr(tasks, 'record_operation_retry', lambda *args, **kwargs: pytest.fail('denial is not failure'))
    monkeypatch.setattr(tasks, 'defer_operation_for_foreground', lambda *args: deferred.append(args))
    with tasks.activity_gate.foreground():
        assert tasks.execute_background_command.run('claim', 'games.analysis.position.claim', {}) is None
    assert deferred == [('claim', 'owned')]


def test_transaction_deadline_bookkeeping_does_not_wait_for_raced_foreground(dispatch_store, monkeypatch):
    from psycopg.errors import TransactionTimeout
    queued = enqueue_daily()
    foreground_started = Event()
    release_foreground = Event()
    def foreground():
        with tasks.activity_gate.foreground():
            foreground_started.set()
            assert release_foreground.wait(2)
    worker = Thread(target=foreground)
    def timed_out(claimed):
        worker.start()
        assert foreground_started.wait(1)
        raise TransactionTimeout('controlled deadline after foreground arrival')
    monkeypatch.setattr(tasks, 'execute_postgres_queue_refresh_slice', timed_out)
    try:
        assert tasks.poll_background_tasks.run() is True
        saved = task_row(queued['id'])
        assert saved['state'] == 'retrying' and saved['transaction_timeout_count'] == 1
        assert saved['attempt_count'] == 0 and saved['lease_token'] is None
        assert 'controlled deadline' in saved['last_error']
    finally:
        release_foreground.set()
        worker.join(1)
    assert not worker.is_alive()
