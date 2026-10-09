"""Issue135: a committed foreground queue request does not depend on beat."""

from contextlib import contextmanager
import socket
import sqlite3
from types import SimpleNamespace
from threading import Thread

from kombu.exceptions import EncodeError, OperationalError as BrokerUnavailable
import pytest

from app import command_gateway, database
from app.services import durable_tasks, queue_refresh_wakeup


@pytest.fixture
def queue_wakeup_store(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "queue-wakeup.sqlite")
    database.initialize()
    return database.DB_PATH


def enqueue_queue(connection):
    return durable_tasks.enqueue_task_in_transaction(connection, "daily_queue", "current",
        {"queue_date": "2026-10-09"}, priority=10)


def test_issue135_queue_wake_occurs_only_after_closed_committed_command(queue_wakeup_store, monkeypatch):
    connection_open = False
    observed_wakes = []

    def committed_command(*args, **kwargs):
        nonlocal connection_open
        connection_open = True
        connection = sqlite3.connect(queue_wakeup_store)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                enqueue_queue(connection)
                assert observed_wakes == []
        finally:
            connection.close()
            connection_open = False
        return {"saved": True}

    def wake_after_commit():
        assert not connection_open
        with sqlite3.connect(queue_wakeup_store) as observer:
            assert observer.execute("SELECT state,lease_token FROM background_tasks").fetchone() == ("queued", None)
        observed_wakes.append(True)

    monkeypatch.setattr(command_gateway, "_execute_command", committed_command)
    monkeypatch.setattr(queue_refresh_wakeup, "wake_queue_refresh", wake_after_commit)
    assert command_gateway.execute_command("save", "settings.update", {}) == {"saved": True}
    assert observed_wakes == [True]


@pytest.mark.parametrize("failure_outcome", ["handled", "raised"])
def test_issue135_queue_wake_does_not_escape_rolled_back_command(queue_wakeup_store, monkeypatch, failure_outcome):
    observed_wakes = []

    def rolled_back_command(*args, **kwargs):
        with sqlite3.connect(queue_wakeup_store) as connection:
            connection.row_factory = sqlite3.Row
            enqueue_queue(connection)
            connection.rollback()
            if failure_outcome == "raised":
                raise RuntimeError("Controlled failed command")
            return None

    monkeypatch.setattr(command_gateway, "_execute_command", rolled_back_command)
    monkeypatch.setattr(queue_refresh_wakeup, "wake_queue_refresh", lambda: observed_wakes.append(True))
    if failure_outcome == "raised":
        with pytest.raises(RuntimeError, match="Controlled failed command"):
            command_gateway.execute_command("rollback", "settings.update", {})
    else:
        assert command_gateway.execute_command("rollback", "settings.update", {}) is None
    with sqlite3.connect(queue_wakeup_store) as observer:
        assert observer.execute("SELECT COUNT(*) FROM background_tasks").fetchone()[0] == 0
    assert observed_wakes == []
    monkeypatch.setattr(command_gateway, "_execute_command", lambda *args, **kwargs: {"saved": True})
    command_gateway.execute_command("later", "unrelated.command", {})
    assert observed_wakes == []


def test_issue135_queue_wake_coalesces_replacements_without_claiming(queue_wakeup_store, monkeypatch):
    observed_wakes = []

    def replacement_command(*args, **kwargs):
        with sqlite3.connect(queue_wakeup_store) as connection:
            connection.row_factory = sqlite3.Row
            for _replacement_number in range(7):
                enqueue_queue(connection)
        return {"saved": True}

    monkeypatch.setattr(command_gateway, "_execute_command", replacement_command)
    monkeypatch.setattr(queue_refresh_wakeup, "wake_queue_refresh", lambda: observed_wakes.append(True))
    command_gateway.execute_command("replace", "settings.update", {})
    with sqlite3.connect(queue_wakeup_store) as observer:
        assert observer.execute("SELECT generation,state,lease_token FROM background_tasks").fetchone() == (7, "queued", None)
    assert observed_wakes == [True]


def test_issue135_queue_ensure_receipt_replay_wakes_without_replacing_work(monkeypatch):
    observed_wakes = []
    # The inner boundary returns the saved receipt without calling its handler.
    monkeypatch.setattr(command_gateway, "_execute_command", lambda *args, **kwargs: {"refresh_pending": True})
    monkeypatch.setattr(queue_refresh_wakeup, "wake_queue_refresh", lambda: observed_wakes.append(True))
    assert command_gateway.execute_command("replay", "queue.ensure_current", {}) == {"refresh_pending": True}
    assert observed_wakes == [True]


def test_issue135_queue_wake_requests_do_not_cross_concurrent_commands():
    worker_requests = []

    def separate_command():
        with queue_refresh_wakeup.capture_queue_refresh_request() as worker_request:
            queue_refresh_wakeup.mark_queue_refresh_requested()
            worker_requests.append(worker_request.requested)

    with queue_refresh_wakeup.capture_queue_refresh_request() as foreground_request:
        worker = Thread(target=separate_command)
        worker.start()
        worker.join(2)
        assert not worker.is_alive()
        assert worker_requests == [True]
        assert not foreground_request.requested
    with queue_refresh_wakeup.capture_queue_refresh_request() as later_request:
        assert not later_request.requested


@pytest.mark.parametrize("failure_stage", ["connection", "publish", "teardown"])
@pytest.mark.parametrize("broker_error", [BrokerUnavailable("Connection refused"),
    ConnectionRefusedError("Connection refused"), socket.timeout("Broker socket timed out"),
    EncodeError("Broker message encoding failed")], ids=["operational", "refused", "timeout", "encode"])
def test_issue135_queue_wake_publish_failure_preserves_committed_result(
    queue_wakeup_store, monkeypatch, caplog, failure_stage, broker_error,
):
    from app.celery_app import celery_app

    def committed_command(*args, **kwargs):
        with sqlite3.connect(queue_wakeup_store) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("UPDATE settings SET new_cards_per_day=17 WHERE id=1")
            enqueue_queue(connection)
        return {"saved": True}

    observed_stages = []

    @contextmanager
    def bounded_connection(**options):
        assert options["connect_timeout"] == 1
        assert options["transport_options"]["socket_timeout"] == 1
        assert options["transport_options"]["socket_connect_timeout"] == 1
        assert options["transport_options"]["max_retries"] == 0
        assert options["transport_options"]["retry_on_timeout"] is False
        observed_stages.append("connection")
        if failure_stage == "connection":
            raise broker_error
        yield SimpleNamespace(name="owned-test-connection")
        observed_stages.append("teardown")
        if failure_stage == "teardown":
            raise broker_error

    def unavailable_wake(name, **options):
        assert name == "app.tasks.poll_background_tasks"
        assert options["queue"] == "background" and options["ignore_result"] is True
        assert options["retry"] is False
        observed_stages.append("publish")
        with sqlite3.connect(queue_wakeup_store) as observer:
            assert observer.execute("SELECT new_cards_per_day FROM settings WHERE id=1").fetchone()[0] == 17
            assert observer.execute("SELECT state FROM background_tasks").fetchone()[0] == "queued"
        if failure_stage == "publish":
            raise broker_error

    monkeypatch.setattr(command_gateway, "_execute_command", committed_command)
    monkeypatch.setattr(celery_app, "connection_for_write", bounded_connection)
    monkeypatch.setattr(celery_app, "send_task", unavailable_wake)
    assert command_gateway.execute_command("wake-loss", "settings.update", {}) == {"saved": True}
    with sqlite3.connect(queue_wakeup_store) as observer:
        assert observer.execute("SELECT generation,state,lease_token FROM background_tasks").fetchone() == (1, "queued", None)
        assert observer.execute("SELECT new_cards_per_day FROM settings WHERE id=1").fetchone()[0] == 17
    assert failure_stage in observed_stages
    assert "durable work remains pending for periodic recovery" in caplog.text
    assert str(broker_error) in caplog.text


@pytest.mark.parametrize("process_control", [KeyboardInterrupt, SystemExit])
def test_issue135_advisory_queue_wake_preserves_process_control(monkeypatch, process_control):
    from app.celery_app import celery_app

    def interrupted_connection(**options):
        raise process_control()

    monkeypatch.setattr(celery_app, "connection_for_write", interrupted_connection)
    with pytest.raises(process_control):
        queue_refresh_wakeup.wake_queue_refresh()
