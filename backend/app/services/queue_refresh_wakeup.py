"""Track queue requests inside a command, then wake after its connection closes."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
import logging


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


def wake_queue_refresh(*, eligible_at: datetime | None = None) -> None:
    """Publish only a capacity wake; PostgreSQL still owns claims and recovery."""

    try:
        from ..celery_app import celery_app
        delivery_options = {}
        if eligible_at is not None:
            if eligible_at.utcoffset() is None:
                raise ValueError("Queue capacity wake eligibility must include a timezone")
            delivery_options["eta"] = eligible_at
        # A disposable connection bounds this advisory publish independently
        # of the command receipt. Never hold its database connection over I/O.
        transport_options = {
            **celery_app.conf.broker_transport_options,
            "socket_connect_timeout": 1, "socket_timeout": 1,
            "max_retries": 0, "retry_on_timeout": False,
        }
        with celery_app.connection_for_write(connect_timeout=1, transport_options=transport_options) as connection:
            celery_app.send_task("app.tasks.poll_background_tasks", queue="background",
                                 connection=connection, retry=False, ignore_result=True,
                                 **delivery_options)
    except Exception:
        # The command has committed. Do not misreport it as failed or undo its
        # receipt. Celery normalizes transport errors only around publication;
        # connection setup, encoding and teardown can raise other Exceptions.
        # Process-control BaseExceptions still propagate.
        _LOGGER.exception(
            "Queue refresh wake unavailable; durable work remains pending for periodic recovery; next_attempt_at=%s",
            eligible_at,
        )
