"""Issue135: a committed foreground queue request does not depend on beat."""

from threading import Thread

import pytest

from app import command_gateway
from app.services import queue_refresh_wakeup


def test_issue135_queue_ensure_receipt_replay_wakes_without_replacing_work(monkeypatch):
    observed_wakes = []
    # The inner boundary returns the saved receipt without calling its handler.
    monkeypatch.setattr(
        command_gateway,
        "_execute_command",
        lambda *args, **kwargs: {"refresh_pending": True},
    )
    monkeypatch.setattr(
        queue_refresh_wakeup, "wake_queue_refresh", lambda: observed_wakes.append(True)
    )
    assert command_gateway.execute_command("replay", "queue.ensure_current", {}) == {
        "refresh_pending": True
    }
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


@pytest.mark.parametrize("process_control", [KeyboardInterrupt, SystemExit])
def test_issue135_advisory_queue_wake_preserves_process_control(
    monkeypatch, process_control
):
    from app.celery_app import celery_app

    def interrupted_connection(**options):
        raise process_control()

    monkeypatch.setattr(celery_app, "connection_for_write", interrupted_connection)
    with pytest.raises(process_control):
        queue_refresh_wakeup.wake_queue_refresh()
