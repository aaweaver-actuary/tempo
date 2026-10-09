"""Track queue requests inside a command, then wake after its connection closes."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import logging

from kombu.exceptions import OperationalError as BrokerUnavailable


@dataclass
class QueueRefreshRequest:
    requested: bool = False


_command_queue_request: ContextVar[QueueRefreshRequest | None] = ContextVar(
    "command_queue_refresh_request", default=None,
)
_LOGGER = logging.getLogger("tempo.queue_refresh")


@contextmanager
def capture_queue_refresh_request():
    request = QueueRefreshRequest()
    context_token = _command_queue_request.set(request)
    try:
        yield request
    finally:
        _command_queue_request.reset(context_token)


def mark_queue_refresh_requested() -> None:
    request = _command_queue_request.get()
    if request is not None:
        request.requested = True


def wake_queue_refresh() -> None:
    """Publish only a capacity wake; PostgreSQL still owns claims and recovery."""

    from ..celery_app import celery_app

    try:
        # A disposable connection bounds this advisory publish independently
        # of the command receipt. Never hold its database connection over I/O.
        transport_options = {
            **celery_app.conf.broker_transport_options,
            "socket_connect_timeout": 1, "socket_timeout": 1,
        }
        with celery_app.connection_for_write(connect_timeout=1, transport_options=transport_options) as connection:
            celery_app.send_task("app.tasks.poll_background_tasks", queue="background",
                                 connection=connection, retry=False, ignore_result=True)
    except BrokerUnavailable:
        # The command has committed. Do not misreport it as failed or undo its
        # receipt; durable work remains eligible for periodic recovery.
        _LOGGER.exception("Queue refresh wake unavailable; durable work remains pending for periodic recovery")
