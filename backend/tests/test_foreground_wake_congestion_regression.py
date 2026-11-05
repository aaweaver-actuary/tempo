"""Focused fake-broker proof of foreground wake ownership under congestion.

This exercises the consumer wrapper and fake Redis ownership only; it is not a
native broker, durable queue claim, or issued-plan integration proof.
"""

from celery import Celery
from kombu.exceptions import OperationalError
import pytest

from app.celery_app import celery_app
from app.services import background_wakes
from test_background_wakes import wake_broker  # reuse the existing fake transport


TASK = "app.tasks.progress_study_queue"


def deliver(token, monkeypatch, executions):
    class ForegroundWake(background_wakes.CoalescedWakeTask):
        name = TASK

        def run(self):
            executions.append(token)
            return True

    task = ForegroundWake()
    task.bind(celery_app)
    task.push_request(id=token, headers={background_wakes.WAKE_HEADER: token})
    try:
        return task()
    finally:
        task.pop_request()


def test_foreground_congestion_preserves_successor_and_fences_stale_delivery(
    wake_broker, monkeypatch
):
    published, values = wake_broker
    first = celery_app.send_task(TASK, queue="foreground")
    assert {celery_app.send_task(TASK, queue="foreground").id for _ in range(100)} == {
        first.id
    }
    assert len(published) == 1
    executions = []
    assert deliver(first.id, monkeypatch, executions)
    # Ownership is consumed before the slice; a wake during that slice owns a successor.
    successor = celery_app.send_task(TASK, queue="foreground")
    assert successor.id != first.id and len(published) == 2
    assert not deliver(first.id, monkeypatch, executions)  # physical stale replay
    assert successor.id in values.values()
    assert deliver(successor.id, monkeypatch, executions)
    assert not deliver(successor.id, monkeypatch, executions)
    assert executions == [first.id, successor.id]


def test_foreground_failed_publication_releases_reservation_for_later_wake(
    wake_broker, monkeypatch
):
    published, values = wake_broker
    original = Celery.send_task
    attempts = []

    def unavailable(*args, **kwargs):
        attempts.append(1)
        raise OperationalError("controlled publication timeout")

    monkeypatch.setattr(Celery, "send_task", unavailable)
    with pytest.raises(OperationalError, match="controlled publication timeout"):
        celery_app.send_task(TASK, queue="foreground")
    assert len(attempts) == 1 and not values and not published
    monkeypatch.setattr(Celery, "send_task", original)
    later = celery_app.send_task(TASK, queue="foreground")
    executions = []
    assert deliver(later.id, monkeypatch, executions)
    assert executions == [later.id]
