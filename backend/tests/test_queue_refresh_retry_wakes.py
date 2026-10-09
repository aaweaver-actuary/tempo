"""Issue135: ETA hints recover deferred queues without a periodic scheduler."""

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import heapq
import sqlite3
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from psycopg.errors import TransactionTimeout

from app import command_gateway, database, tasks
from app.services import durable_tasks, queue_refresh_wakeup


@pytest.fixture
def retry_wakes(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "retry-wakes.sqlite")
    database.initialize()
    observed_clock = [datetime(2026, 10, 9, 8, tzinfo=timezone.utc)]
    monkeypatch.setattr(durable_tasks, "_now", lambda: observed_clock[0])

    class WakeClock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is not None
            return observed_clock[0].astimezone(tz)

    monkeypatch.setattr(queue_refresh_wakeup, "datetime", WakeClock)
    monkeypatch.setattr(tasks, "measure_handler", lambda *args: nullcontext())
    connection_open = False
    deliveries, publications, executions, poll_results = [], [], [], []

    def write(operation, *, label):
        nonlocal connection_open
        try:
            with database.connection(background=True) as connection:
                connection_open = True
                return operation(connection)
        finally:
            connection_open = False

    monkeypatch.setattr(durable_tasks, "submit_background_write", write)
    monkeypatch.setattr(durable_tasks, "submit_foreground_write", write)
    monkeypatch.setattr(tasks.celery_app, "connection_for_write", lambda **options: nullcontext(object()))

    def state():
        # An independent observer does not participate in worker admission.
        with sqlite3.connect(database.DB_PATH) as connection:
            connection.row_factory = sqlite3.Row
            task = connection.execute("SELECT * FROM background_tasks WHERE kind='daily_queue'").fetchone()
            projection = connection.execute("SELECT * FROM queue_projections").fetchone()
            return dict(task) if task else None, dict(projection) if projection else None

    def publish(name, **options):
        assert not connection_open
        assert name == "app.tasks.poll_background_tasks" and options["queue"] == "background"
        assert not options.get("args") and not options.get("kwargs") and "task_id" not in options
        delivery_at = options.get("eta", observed_clock[0])
        assert delivery_at.tzinfo is not None
        if "eta" in options:
            saved_task, _projection = state()
            assert saved_task["state"] in {"queued", "retrying"} and saved_task["lease_token"] is None
            assert options["retry"] is False and options["ignore_result"] is True
        publications.append((delivery_at, dict(options)))
        heapq.heappush(deliveries, (delivery_at, len(publications)))

    monkeypatch.setattr(tasks.celery_app, "send_task", publish)

    def command(*args, **kwargs):
        with database.connection() as connection:
            task = durable_tasks.enqueue_task_in_transaction(connection, "daily_queue", "current",
                {"queue_date": "2026-10-09"}, priority=10)
            connection.execute("INSERT INTO queue_projections(queue_date,state,generation,refresh_pending) "
                "VALUES('2026-10-09','refreshing',?,1) ON CONFLICT(queue_date) DO UPDATE SET "
                "state='refreshing',generation=excluded.generation,refresh_pending=1", (task["generation"],))
        return {"saved": True}

    monkeypatch.setattr(command_gateway, "_execute_command", command)

    def slice_once(claimed):
        executions.append((claimed["generation"], observed_clock[0]))
        if len(executions) == 1:
            raise TransactionTimeout("Controlled first queue slice deadline")
        with database.connection(background=True) as connection:
            assert durable_tasks.complete_task_slice_in_transaction(connection, claimed)
            durable_tasks.update_queue_refresh_status_in_transaction(connection, "daily_queue",
                claimed["payload"], state="ready")
        return False

    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", slice_once)

    def deliver_due(*, one=False):
        # The driver only consumes published messages. There are no beat ticks
        # or direct test calls to poll_background_tasks outside this delivery.
        for _delivery_number in range(100):
            if not deliveries or deliveries[0][0] > observed_clock[0]:
                return
            delivery_at, publication_number = heapq.heappop(deliveries)
            delivery_options = publications[publication_number - 1][1]
            tasks.poll_background_tasks.push_request(headers=delivery_options.get("headers"))
            try:
                poll_results.append((delivery_at, tasks.poll_background_tasks.run()))
            finally:
                tasks.poll_background_tasks.pop_request()
            if one:
                return
        raise AssertionError("Unexpected unbounded capacity wake loop")

    return SimpleNamespace(clock=observed_clock, deliveries=deliveries, publications=publications,
        executions=executions, polls=poll_results, state=state, deliver_due=deliver_due)


def test_issue135_foreground_denied_queue_wake_retains_future_delivery_without_periodic_polling(
    retry_wakes, monkeypatch,
):
    driver = retry_wakes
    finished = Event()
    worker_errors = []

    def consume_wake():
        try:
            driver.deliver_due()
        except BaseException as error:
            worker_errors.append(error)
        finally:
            finished.set()

    original_claim = tasks.claim_task
    claims = []

    def claim_when_idle(**options):
        assert not tasks.activity_gate.foreground_waiting
        claims.append(True)
        return original_claim(**options)

    def complete_queue(claimed):
        driver.executions.append((claimed["generation"], driver.clock[0]))
        with database.connection(background=True) as connection:
            assert durable_tasks.complete_task_slice_in_transaction(connection, claimed)
            durable_tasks.update_queue_refresh_status_in_transaction(connection, "daily_queue",
                claimed["payload"], state="ready")
        return False

    monkeypatch.setattr(tasks, "claim_task", claim_when_idle)
    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", complete_queue)
    monkeypatch.setattr(tasks.time, "sleep", lambda *_args: pytest.fail("Admission denial must not sleep"))
    with tasks.activity_gate.foreground():
        command_gateway.execute_command("foreground-commit", "settings.update", {})
        before = driver.state()
        worker = Thread(target=consume_wake)
        worker.start()
        assert finished.wait(1), "Admission denial occupied the worker"
        worker.join(1)
        assert not worker.is_alive() and worker_errors == []
        assert driver.state() == before
        assert before[0]["attempt_count"] == 0 and before[0]["lease_token"] is None
        assert before[0]["lease_expires_at"] is None and before[0]["last_error"] is None
        assert claims == [] and driver.executions == []
        assert len(driver.deliveries) == 1, "Denied committed queue wake lost its future delivery"
        assert driver.deliveries[0][0] == driver.clock[0] + timedelta(seconds=1)
        assert len(driver.publications) == 2
        for _denial_number in range(3):
            eligibility = driver.deliveries[0][0]
            driver.clock[0] = eligibility - timedelta(microseconds=1)
            poll_count = len(driver.polls)
            driver.deliver_due()
            assert len(driver.polls) == poll_count
            driver.clock[0] = eligibility
            driver.deliver_due()
            assert len(driver.polls) == poll_count + 1
            assert len(driver.deliveries) == 1
            assert driver.deliveries[0][0] == eligibility + timedelta(seconds=1)
            assert driver.state() == before and claims == [] and driver.executions == []
    driver.clock[0] = driver.deliveries[0][0]
    driver.deliver_due()
    saved_task, projection = driver.state()
    assert claims == [True]
    assert driver.executions == [(before[0]["generation"], driver.clock[0])]
    assert saved_task["generation"] == before[0]["generation"] and saved_task["state"] == "complete"
    assert (projection["state"], projection["refresh_pending"], projection["last_error"]) == ("ready", 0, None)
    assert driver.deliveries == []


def test_issue135_queue_wake_continuation_survives_foreground_denial(retry_wakes, monkeypatch):
    driver = retry_wakes

    def checkpoint_then_complete(claimed):
        driver.executions.append((claimed["generation"], driver.clock[0]))
        with database.connection(background=True) as connection:
            if claimed["phase"] != "publish_projection":
                assert durable_tasks.advance_task_slice_in_transaction(connection, claimed,
                    next_phase="publish_projection", next_payload={**claimed["payload"], "after_card_id": "saved"})
                return True
            assert claimed["payload"]["after_card_id"] == "saved"
            assert durable_tasks.complete_task_slice_in_transaction(connection, claimed)
            durable_tasks.update_queue_refresh_status_in_transaction(connection, "daily_queue",
                claimed["payload"], state="ready")
        return False

    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", checkpoint_then_complete)
    command_gateway.execute_command("checkpoint", "settings.update", {})
    driver.deliver_due(one=True)
    checkpoint = driver.state()
    assert checkpoint[0]["phase"] == "publish_projection"
    assert len(driver.deliveries) == 1
    assert driver.publications[-1][1]["headers"]["queue_refresh_wake"] is True
    with tasks.activity_gate.foreground():
        driver.deliver_due()
        assert driver.state() == checkpoint and len(driver.executions) == 1
        assert len(driver.deliveries) == 1
        assert driver.deliveries[0][0] == driver.clock[0] + timedelta(seconds=1)
    driver.clock[0] = driver.deliveries[0][0]
    driver.deliver_due()
    assert len(driver.executions) == 2 and driver.state()[0]["state"] == "complete"
    assert driver.state()[1]["state"] == "ready" and driver.deliveries == []


@pytest.mark.parametrize("replace_generation", [False, True], ids=["resume", "stale-deferral"])
def test_issue135_queue_wake_raced_claim_preserves_checkpoint_and_fences_deferral(
    retry_wakes, monkeypatch, replace_generation,
):
    driver = retry_wakes
    command_gateway.execute_command("raced-claim", "settings.update", {})
    with database.connection() as connection:
        connection.execute("UPDATE background_tasks SET phase='publish_projection',payload_json=?",
            ('{"queue_date":"2026-10-09","after_card_id":"saved"}',))
    original = driver.state()[0]
    original_claim = tasks.claim_task
    foreground_entered = Event()
    release_foreground = Event()
    foreground_errors = []

    def hold_foreground():
        try:
            with tasks.activity_gate.foreground():
                if replace_generation:
                    # Commit a replacement without injecting an extra wake.
                    with queue_refresh_wakeup.capture_queue_refresh_request():
                        command_gateway._execute_command("replacement", "settings.update", {})
                foreground_entered.set()
                assert release_foreground.wait(5), "Raced-claim proof did not release foreground"
        except BaseException as error:
            foreground_errors.append(error)

    foreground_worker = Thread(target=hold_foreground)

    def claim_before_foreground(**options):
        claimed = original_claim(**options)
        foreground_worker.start()
        assert foreground_entered.wait(1)
        return claimed

    monkeypatch.setattr(tasks, "claim_task", claim_before_foreground)
    try:
        driver.deliver_due()
        saved, _projection = driver.state()
        assert saved["attempt_count"] == 0 and saved["lease_token"] is None
        assert saved["lease_expires_at"] is None and saved["last_error"] is None
        assert driver.executions == []
        if replace_generation:
            assert saved["generation"] == original["generation"] + 1 and saved["state"] == "queued"
            assert len(driver.publications) == 1 and driver.deliveries == []
        else:
            assert saved["generation"] == original["generation"] and saved["state"] == "retrying"
            assert saved["phase"] == original["phase"] and saved["payload_json"] == original["payload_json"]
            assert len(driver.deliveries) == 1
            assert driver.deliveries[0][0] == datetime.fromisoformat(saved["next_attempt_at"])
    finally:
        release_foreground.set()
        if foreground_worker.ident is not None:
            foreground_worker.join(1)
        assert not foreground_worker.is_alive() and foreground_errors == []
    monkeypatch.setattr(tasks, "claim_task", original_claim)
    if not replace_generation:
        def complete_saved_checkpoint(claimed):
            assert claimed["payload"]["after_card_id"] == "saved"
            driver.executions.append((claimed["generation"], driver.clock[0]))
            with database.connection(background=True) as connection:
                assert durable_tasks.complete_task_slice_in_transaction(connection, claimed)
                durable_tasks.update_queue_refresh_status_in_transaction(connection, "daily_queue",
                    claimed["payload"], state="ready")
            return False
        monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", complete_saved_checkpoint)
        driver.clock[0] = driver.deliveries[0][0]
        driver.deliver_due()
        assert driver.executions == [(original["generation"], driver.clock[0])]
        assert driver.state()[0]["state"] == "complete" and driver.state()[1]["state"] == "ready"
        assert driver.deliveries == []


def test_issue135_foreground_delayed_wake_claims_only_current_generation_once(retry_wakes, monkeypatch):
    driver = retry_wakes
    with tasks.activity_gate.foreground():
        command_gateway.execute_command("original", "settings.update", {})
        original = driver.state()[0]
        driver.deliver_due()
    with queue_refresh_wakeup.capture_queue_refresh_request():
        command_gateway._execute_command("replacement", "settings.update", {})
    replacement = driver.state()[0]
    assert replacement["generation"] == original["generation"] + 1

    def complete_replacement(claimed):
        driver.executions.append((claimed["generation"], driver.clock[0]))
        with database.connection(background=True) as connection:
            assert durable_tasks.complete_task_slice_in_transaction(connection, claimed)
            durable_tasks.update_queue_refresh_status_in_transaction(connection, "daily_queue",
                claimed["payload"], state="ready")
        return False

    monkeypatch.setattr(tasks, "execute_postgres_queue_refresh_slice", complete_replacement)
    driver.clock[0] = driver.deliveries[0][0]
    heapq.heappush(driver.deliveries, driver.deliveries[0])
    driver.deliver_due()
    assert driver.executions == [(replacement["generation"], driver.clock[0])]
    assert driver.state()[0]["state"] == "complete" and driver.state()[1]["state"] == "ready"
    assert driver.deliveries == []


def test_issue135_unmarked_periodic_poll_does_not_schedule_foreground_retry(retry_wakes):
    driver = retry_wakes
    with tasks.activity_gate.foreground():
        assert tasks.poll_background_tasks.run() is False
    assert driver.publications == [] and driver.deliveries == [] and driver.executions == []


def test_issue135_foreground_retry_broker_failure_preserves_unclaimed_intent(retry_wakes, monkeypatch, caplog):
    from kombu.exceptions import EncodeError

    driver = retry_wakes
    with tasks.activity_gate.foreground():
        command_gateway.execute_command("committed", "settings.update", {})
        before = driver.state()
        def unavailable_retry(*_args, **_options):
            raise EncodeError("Controlled foreground retry broker failure")
        monkeypatch.setattr(tasks.celery_app, "send_task", unavailable_retry)
        driver.deliver_due()
        assert driver.state() == before and driver.executions == []
        assert driver.polls == [(driver.clock[0], False)]
    assert "Controlled foreground retry broker failure" in caplog.text


def test_issue135_deferred_queue_recovers_without_periodic_polling(retry_wakes):
    driver = retry_wakes
    assert command_gateway.execute_command("request", "settings.update", {}) == {"saved": True}
    assert len(driver.publications) == 1 and "eta" not in driver.publications[0][1]
    driver.deliver_due()
    saved_task, projection = driver.state()
    eligibility = datetime.fromisoformat(saved_task["next_attempt_at"])
    assert eligibility == driver.clock[0] + timedelta(seconds=1)
    assert saved_task["state"] == "retrying" and projection["state"] == "refreshing"
    assert projection["last_error"] == "Controlled first queue slice deadline"
    assert driver.polls == [(driver.clock[0], True), (driver.clock[0], False)]
    assert len(driver.deliveries) == 1 and driver.deliveries[0][0] == eligibility
    driver.clock[0] = eligibility - timedelta(microseconds=1)
    driver.deliver_due()
    assert len(driver.executions) == 1
    driver.clock[0] = eligibility
    # Broker duplicate of the same published delayed capacity hint.
    heapq.heappush(driver.deliveries, driver.deliveries[0])
    driver.deliver_due()
    saved_task, projection = driver.state()
    assert driver.executions == [(saved_task["generation"], eligibility - timedelta(seconds=1)),
        (saved_task["generation"], eligibility)]
    assert saved_task["state"] == "complete"
    assert (projection["state"], projection["refresh_pending"], projection["last_error"]) == ("ready", 0, None)
    assert driver.polls[-1] == (eligibility, False)


def test_issue135_delayed_capacity_wake_cannot_execute_replaced_generation(retry_wakes):
    driver = retry_wakes
    command_gateway.execute_command("original", "settings.update", {})
    driver.deliver_due()
    original, _projection = driver.state()
    eligibility = datetime.fromisoformat(original["next_attempt_at"])
    assert len(driver.deliveries) == 1
    # Replace before the old ETA, then let that old delivery claim current work.
    with queue_refresh_wakeup.capture_queue_refresh_request():
        command_gateway._execute_command("replacement", "settings.update", {})
    replacement, _projection = driver.state()
    assert replacement["generation"] == original["generation"] + 1
    driver.clock[0] = eligibility
    driver.deliver_due()
    assert [generation for generation, _time in driver.executions] == [original["generation"], replacement["generation"]]
    assert driver.state()[0]["state"] == "complete" and driver.state()[1]["state"] == "ready"


def test_issue135_earlier_progress_makes_old_delayed_wake_harmless(retry_wakes):
    driver = retry_wakes
    command_gateway.execute_command("original", "settings.update", {})
    driver.deliver_due()
    original, _projection = driver.state()
    eligibility = datetime.fromisoformat(original["next_attempt_at"])
    command_gateway.execute_command("replacement", "settings.update", {})
    driver.deliver_due()
    completed = driver.state()
    assert completed[0]["generation"] == original["generation"] + 1
    assert completed[0]["state"] == "complete"
    driver.clock[0] = eligibility
    driver.deliver_due()
    assert driver.state() == completed and len(driver.executions) == 2


def test_issue135_rejected_or_rolled_back_deferral_emits_no_delayed_wake(retry_wakes, monkeypatch):
    driver = retry_wakes
    command_gateway.execute_command("original", "settings.update", {})
    claimed = durable_tasks.claim_task("daily_queue")
    before = driver.state()
    publication_count = len(driver.publications)
    original_event = durable_tasks._record_event

    def interrupted_event(*args, **kwargs):
        original_event(*args, **kwargs)
        raise RuntimeError("Controlled deferral commit failure")

    with monkeypatch.context() as rollback:
        rollback.setattr(durable_tasks, "_record_event", interrupted_event)
        with pytest.raises(RuntimeError, match="Controlled deferral commit failure"):
            durable_tasks.defer_task_for_transaction_timeout(claimed, TransactionTimeout("Deadline"))
    assert driver.state() == before and len(driver.publications) == publication_count
    command_gateway.execute_command("replacement", "settings.update", {})
    publication_count = len(driver.publications)
    assert durable_tasks.defer_task_for_transaction_timeout(claimed, TransactionTimeout("Stale deadline")) is False
    assert len(driver.publications) == publication_count


def test_issue135_delayed_wake_failure_retains_committed_deferral(retry_wakes, monkeypatch, caplog):
    from kombu.exceptions import EncodeError

    driver = retry_wakes
    command_gateway.execute_command("original", "settings.update", {})
    claimed = durable_tasks.claim_task("daily_queue")

    def unavailable_wake(*args, **kwargs):
        raise EncodeError("Controlled delayed wake failure")

    monkeypatch.setattr(tasks.celery_app, "send_task", unavailable_wake)
    assert durable_tasks.defer_task_for_transaction_timeout(claimed, TransactionTimeout("Deadline")) is True
    saved_task, projection = driver.state()
    assert saved_task["state"] == "retrying" and saved_task["lease_token"] is None
    assert datetime.fromisoformat(saved_task["next_attempt_at"]) == driver.clock[0] + timedelta(seconds=1)
    assert projection["last_error"] == "Deadline"
    assert "Controlled delayed wake failure" in caplog.text
    assert str(datetime.fromisoformat(saved_task["next_attempt_at"])) in caplog.text
