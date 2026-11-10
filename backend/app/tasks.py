"""Celery entry points; task payloads carry named commands, never callables."""

from __future__ import annotations

from contextlib import nullcontext, contextmanager
import os

import logging
import time
import uuid
from datetime import date
from typing import Any

from kombu.exceptions import OperationalError as BrokerUnavailable
import psycopg
from psycopg.errors import (
    DeadlockDetected,
    LockNotAvailable,
    SerializationFailure,
    TransactionTimeout,
)
from psycopg_pool import PoolTimeout
from functools import wraps


from .product_composition.database_infrastructure import bind_database_infrastructure

bind_database_infrastructure()

from .contracts.command_registry import (
    RETIRED_COMMANDS,
    RETIRED_TASK_KINDS,
    is_retired_command,
)
from .celery_app import celery_app
from .command_gateway import (
    claim_recoverable_operation,
    execute_command,
    record_operation_attempt,
    record_operation_retry,
    defer_operation_for_foreground,
)
from tempo_storage.connections import read_connection
from tempo_storage.lifecycle.refresh_requests import (
    daily_queue_projection,
)
from . import study_attempt_commands  # noqa: F401 - registers Study attempt commands
from . import tactic_commands  # noqa: F401 - registers tactic attempt commands
from . import queue_commands  # noqa: F401 - registers foreground queue commands

from . import review_commands  # noqa: F401 - registers foreground review command
from .command_gateway import register_command
from .study.position_checkpoint import prepare_standalone_checkpoint, commit_standalone_checkpoint
register_command("opening_evidence.checkpoint", commit_standalone_checkpoint, prepare=prepare_standalone_checkpoint)

from . import teaching_commands  # noqa: F401 - registers foreground teaching command
from . import repertoire_commands  # noqa: F401 - registers foreground repertoire command
from . import settings_commands  # noqa: F401 - registers foreground settings command
from . import annotation_commands  # noqa: F401 - registers foreground position notes
from . import branch_commands  # noqa: F401 - registers foreground branch edits
from . import pgn_import_commands  # noqa: F401 - registers foreground PGN imports
from . import analysis_paste_commands  # noqa: F401 - registers foreground pasted lines
from . import prefix_split_commands  # noqa: F401 - registers foreground prefix splits
from . import card_commands  # noqa: F401 - registers foreground card revisions
from . import activity_commands  # noqa: F401 - registers foreground activity controls
from . import integrity_repair_commands  # noqa: F401 - registers guided integrity repairs
from .services.activity_gate import activity_gate
from .services.redis_admission_gate import BackgroundAdmissionDeferred
from .services.background_runtime import measure_handler
from .services.background_wakes import CoalescedWakeTask
from .services.durable_tasks import current_delivery, record_stale_delivery
from .services.durable_tasks import (
    claim_task,
    defer_task_for_contention,
    defer_task_for_transaction_timeout,
    fail_task,
)

from .services.durable_tasks import defer_task_for_foreground
from .services.queue_refresh_wakeup import (
    wake_queue_refresh,
    wake_queue_refresh_after_foreground_denial,
)
from .services.queue_ordering import PRESENTATION_VERSION
from .services.postgres_queue_refresh import execute_postgres_queue_refresh_slice
from .services.postgres_opening_graph import execute_postgres_opening_graph_slice
from .services.postgres_integrity import execute_postgres_integrity_slice


_LOGGER = logging.getLogger("tempo.tasks")
from .services import study_mode
from .services.study_mode import (
    STUDY_TASK_KINDS,
    STUDY_BACKGROUND_COMMANDS,
    PAUSED_IMPORT_COMMANDS,
)


def study_only_enabled(database=None):
    """Compatibility seam; always consult the current Study policy owner."""
    return (
        study_mode.study_only_enabled()
        if database is None
        else study_mode.study_only_enabled(database)
    )






# Only bounded accepted results/releases may bypass foreground admission.
# These handlers publish bounded receipts; none starts a search or traversal.
# Maia submit aggregates a whole run; threat report fans out to all candidates.
# Their source receipts remain durable, but execution needs normal admission.
_CONTROL_BACKGROUND_COMMANDS = frozenset()
_SUPPORTED_BACKGROUND_KINDS = (
    "daily_queue",
    "opening_graph_rebuild",
    "integrity_scan",
)


@celery_app.task(name="app.tasks.ensure_daily_queue", base=CoalescedWakeTask)
def ensure_daily_queue() -> bool:
    """Start the new day's queue even when the API stays up past midnight."""

    from .queue_commands import current_study_day

    with activity_gate.foreground():
        with read_connection() as database:
            queue_date = current_study_day(database)
            projection = daily_queue_projection(database, queue_date)
        if (
            projection
            and projection["state"] == "ready"
            and not projection["refresh_pending"]
            and (
                not projection["has_queued_entries"]
                or (projection["presentation_membership_hash"] or "").startswith(
                    PRESENTATION_VERSION + ":"
                )
            )
        ):
            return False
        result = execute_command(
            uuid.uuid4().hex,
            "queue.ensure_current",
            {"queue_date": queue_date},
        )
        if result is None:
            raise RuntimeError("Could not request today's queue refresh")
        return bool(result["refresh_pending"])




@celery_app.task(name="app.tasks.execute_foreground_command", bind=True)
def execute_foreground_command(
    self,
    operation_id: str,
    command_name: str,
    payload: dict[str, Any],
    expected_retry_cycle: int | None = None,
) -> Any:
    from .services.background_runtime import measure_training_timing

    with measure_training_timing(command_name, operation_id) as measurement:
        submitted_at = (self.request.headers or {}).get("submitted_at")
        if measurement is not None and submitted_at:
            measurement["broker_wait_ms"] = (
                max(0.0, time.time() - float(submitted_at)) * 1000
            )
        return _execute_foreground_command(
            self, operation_id, command_name, payload, expected_retry_cycle
        )


def _execute_foreground_command(
    self, operation_id, command_name, payload, expected_retry_cycle=None
):
    if is_retired_command(command_name):
        return None
    submitted_at = (self.request.headers or {}).get("submitted_at")
    queue_wait = max(0.0, time.time() - float(submitted_at)) if submitted_at else None
    started = time.perf_counter()
    with activity_gate.foreground():
        should_execute, saved_payload, attempt_token, _attempt_number = (
            record_operation_attempt(
                operation_id,
                command_name,
                payload,
                background=False,
                expected_retry_cycle=expected_retry_cycle,
            )
        )
        if not should_execute:
            return None
        try:
            if command_name in PAUSED_IMPORT_COMMANDS and study_only_enabled():
                defer_operation_for_foreground(operation_id, attempt_token)
                return None
            result = execute_command(
                operation_id, command_name, saved_payload, attempt_token=attempt_token
            )
        except BackgroundAdmissionDeferred:
            defer_operation_for_foreground(operation_id, attempt_token)
            return None
        except Exception as error:
            record_operation_retry(
                operation_id,
                attempt_token,
                error,
                retryable=False,
                background=False,
            )
            raise
    _LOGGER.info(
        "foreground command=%s queue_wait_seconds=%s execution_seconds=%.3f",
        command_name,
        f"{queue_wait:.3f}" if queue_wait is not None else "unknown",
        time.perf_counter() - started,
    )
    return result


@celery_app.task(name="app.tasks.execute_background_command", bind=True)
def execute_background_command(
    self,
    operation_id: str,
    command_name: str,
    payload: dict[str, Any],
    expected_retry_cycle: int | None = None,
) -> Any:
    """Execute an external worker callback in a bounded background section."""

    if is_retired_command(command_name):
        return None
    with (
        measure_handler("other", (self.request.headers or {}).get("submitted_at")),
        activity_gate.background_job(command_name, operation_id, yielding=True),
    ):
        # Persist the source envelope even when discretionary execution is denied.
        with activity_gate.background_control():
            should_execute, saved_payload, attempt_token, attempt_number = (
                record_operation_attempt(
                    operation_id,
                    command_name,
                    payload,
                    background=True,
                    expected_retry_cycle=expected_retry_cycle,
                )
            )
        if not should_execute:
            return None
        try:
            if study_only_enabled() and command_name not in STUDY_BACKGROUND_COMMANDS:
                defer_operation_for_foreground(operation_id, attempt_token)
                return None
            with (
                activity_gate.background_control()
                if command_name in _CONTROL_BACKGROUND_COMMANDS
                else nullcontext()
            ):
                activity_gate.check_background_admission()
                return execute_command(
                    operation_id,
                    command_name,
                    saved_payload,
                    background=True,
                    attempt_token=attempt_token,
                )
        except BackgroundAdmissionDeferred:
            defer_operation_for_foreground(operation_id, attempt_token)
            return None
        except (
            psycopg.OperationalError,
            psycopg.InterfaceError,
            DeadlockDetected,
            LockNotAvailable,
            SerializationFailure,
            TransactionTimeout,
        ) as error:
            with activity_gate.background_control():
                exhausted, delay_seconds = record_operation_retry(
                    operation_id,
                    attempt_token,
                    error,
                    retryable=True,
                    background=True,
                )
            _LOGGER.warning(
                "background command=%s operation_id=%s attempt=%s stage=execute "
                "error_class=%s next_retry_seconds=%s",
                command_name,
                operation_id,
                attempt_number,
                type(error).__name__,
                delay_seconds,
            )
            return None
        except Exception as error:
            with activity_gate.background_control():
                record_operation_retry(
                    operation_id, attempt_token, error, retryable=False, background=True
                )
            raise


_maintenance_cooldowns = {}




def bounded_maintenance(function):
    """Setup failures retain durable work and release worker capacity with backoff."""

    @wraps(function)
    def bounded(*args, **kwargs):
        failures, retry_at = _maintenance_cooldowns.get(function.__name__, (0, 0))
        if time.monotonic() < retry_at:
            return False
        try:
            result = function(*args, **kwargs)
        except (
            TransactionTimeout,
            psycopg.OperationalError,
            psycopg.InterfaceError,
            PoolTimeout,
        ) as error:
            failures += 1
            delay = min(60, 2 ** min(failures - 1, 6))
            _maintenance_cooldowns[function.__name__] = (
                failures,
                time.monotonic() + delay,
            )
            _LOGGER.warning(
                "maintenance=%s deferred=%s retry_seconds=%s; durable work retained",
                function.__name__,
                type(error).__name__,
                delay,
            )
            return False
        _maintenance_cooldowns.pop(function.__name__, None)
        return result

    return bounded




@celery_app.task(name="app.tasks.recover_operations", base=CoalescedWakeTask)
@bounded_maintenance
def recover_operations() -> bool:
    with (
        activity_gate.background_job("operation_recovery", "one-run", yielding=True),
        activity_gate.background_control(),
    ):
        study_only_mode = study_only_enabled()
        operation = claim_recoverable_operation(
            allowed_background_commands=STUDY_BACKGROUND_COMMANDS
            if study_only_mode
            else None,
            paused_commands=RETIRED_COMMANDS
            | (PAUSED_IMPORT_COMMANDS if study_only_mode else frozenset()),
            background_only=True,
        )
    if operation is None:
        return False
    task_is_background = operation["background"]
    celery_app.send_task(
        "app.tasks.execute_background_command"
        if task_is_background
        else "app.tasks.execute_foreground_command",
        args=[
            operation["operation_id"],
            operation["command_name"],
            operation["payload"],
        ],
        task_id=operation["operation_id"],
        queue="background" if task_is_background else "foreground",
        headers={"submitted_at": time.time()},
    )
    return True


@celery_app.task(name="app.tasks.recover_study_operations", base=CoalescedWakeTask)
@bounded_maintenance
def recover_study_operations() -> bool:
    """Recover one foreground receipt without optional worker/idle dependence."""
    with (
        activity_gate.background_job("operation_recovery", "essential", yielding=True),
        activity_gate.background_control(),
    ):
        operation = claim_recoverable_operation(
            allowed_background_commands=(),
            paused_commands=RETIRED_COMMANDS
            | (PAUSED_IMPORT_COMMANDS if study_only_enabled() else frozenset()),
        )
    if operation is None:
        return False
    if operation["background"]:
        raise ValueError("Essential recovery cannot dispatch optional background work")
    celery_app.send_task(
        "app.tasks.execute_foreground_command",
        args=[
            operation["operation_id"],
            operation["command_name"],
            operation["payload"],
        ],
        task_id=operation["operation_id"],
        queue="foreground",
        headers={"submitted_at": time.time()},
    )
    return True


@celery_app.task(
    name="app.tasks.poll_background_tasks", bind=True, base=CoalescedWakeTask
)
@bounded_maintenance
def poll_background_tasks(self) -> bool:
    """Claim and run one slice at worker capacity, then yield to the broker."""

    queue_refresh_wake = (self.request.headers or {}).get("queue_refresh_wake") is True
    try:
        activity_gate.check_background_admission()
        with activity_gate.background_job("dispatch", "one-run", yielding=True):
            # New essential leases belong to the foreground owner. Keep the
            # already-queued legacy executor below for fenced old deliveries.
            allowed_kinds = tuple(
                kind
                for kind in (
                    STUDY_TASK_KINDS
                    if study_only_enabled()
                    else _SUPPORTED_BACKGROUND_KINDS
                )
                if kind != "daily_queue"
            )
            claimed_task = (
                claim_task(allowed_kinds=allowed_kinds) if allowed_kinds else None
            )
    except BackgroundAdmissionDeferred as error:
        _LOGGER.info("background admission deferred: %s", error)
        if queue_refresh_wake:
            wake_queue_refresh_after_foreground_denial()
        return False
    if claimed_task is None and study_only_enabled():
        try:
            activity_gate.check_background_admission()
            with activity_gate.background_job(
                "dispatch", "explicit-import", yielding=True
            ):
                claimed_task = claim_task(explicit_imports_only=True)
        except BackgroundAdmissionDeferred:
            return False
    if claimed_task is None:
        return False
    return _execute_claimed_background_slice(
        claimed_task,
        (self.request.headers or {}).get("submitted_at"),
        queue_refresh_wake=queue_refresh_wake,
    )


@celery_app.task(name="app.tasks.progress_study_queue", base=CoalescedWakeTask)
@bounded_maintenance
def progress_study_queue() -> bool:
    """Claim exactly one essential queue slice without optional idle admission."""
    with (
        activity_gate.essential_study_progress("daily_queue"),
        activity_gate.background_job("daily_queue", "essential", yielding=True),
    ):
        claimed = claim_task("daily_queue")
        if claimed is None:
            return False
        if claimed["kind"] != "daily_queue":
            raise ValueError("Essential queue dispatch cannot execute an optional task")
        return _execute_claimed_background_slice(claimed, None, queue_refresh_wake=True)


@celery_app.task(name="app.tasks.execute_background_slice", bind=True)
def execute_background_slice(self, claimed_task: dict[str, Any]) -> bool:
    """Process fenced legacy deliveries already queued before execution-time claims."""
    return _execute_claimed_background_slice(
        claimed_task,
        (self.request.headers or {}).get("submitted_at"),
    )


def _execute_claimed_background_slice(
    claimed_task: dict[str, Any],
    submitted_at: float | None,
    *,
    queue_refresh_wake: bool = False,
) -> bool:
    background_handlers = {
        "daily_queue": execute_postgres_queue_refresh_slice,
        "opening_graph_rebuild": execute_postgres_opening_graph_slice,
        "integrity_scan": execute_postgres_integrity_slice,
    }
    if claimed_task["kind"] in RETIRED_TASK_KINDS:
        return False
    handler = background_handlers.get(claimed_task["kind"])
    if handler is None:
        raise ValueError(f"Unported background handler: {claimed_task['kind']}")
    with (
        measure_handler(claimed_task["kind"], submitted_at),
        activity_gate.background_job(
            claimed_task["kind"], claimed_task["id"], yielding=True
        ),
    ):
        try:
            activity_gate.check_background_admission()
            explicit_origin = claimed_task.get("payload", {}).get(
                "origin_import_operation_id"
            )
            if claimed_task["kind"] not in STUDY_TASK_KINDS and (
                study_only_enabled() or explicit_origin
            ):
                from .services.explicit_repertoire_import import permitted_delivery

                if not permitted_delivery(claimed_task):
                    defer_task_for_foreground(claimed_task)
                    return False
                claimed_task = {**claimed_task, "explicit_import_preparation": True}
            if not current_delivery(claimed_task):
                record_stale_delivery(claimed_task)
                return False
            more_work = handler(claimed_task)
        except BackgroundAdmissionDeferred:
            deferred = defer_task_for_foreground(claimed_task)
            if deferred and queue_refresh_wake:
                wake_queue_refresh_after_foreground_denial()
            return False
        except TransactionTimeout as error:
            if claimed_task["kind"] == "daily_queue":
                more_work = defer_task_for_transaction_timeout(claimed_task, error)
            elif claimed_task["kind"] in {
                "opening_graph_rebuild",
            }:
                retry_result = fail_task(
                    claimed_task["id"],
                    claimed_task["generation"],
                    claimed_task["lease_token"],
                    error,
                )
                more_work = retry_result["state"] == "retrying"
                # Celery completion acknowledges this delivery; durable state owns retries.
                _LOGGER.warning(
                    "background_slice_timeout kind=%s phase=%s outcome=%s sqlstate=%s next_attempt_at=%s",
                    claimed_task["kind"],
                    claimed_task.get("phase", "unknown"),
                    retry_result["state"],
                    error.sqlstate or "unknown",
                    retry_result.get("next_attempt_at") if more_work else None,
                )
            else:
                defer_task_for_contention(
                    claimed_task["id"],
                    claimed_task["generation"],
                    claimed_task["lease_token"],
                    kind=claimed_task["kind"],
                )
                more_work = True
        except (DeadlockDetected, LockNotAvailable, SerializationFailure):
            defer_task_for_contention(
                claimed_task["id"],
                claimed_task["generation"],
                claimed_task["lease_token"],
                kind=claimed_task["kind"],
            )
            more_work = True
        except Exception as error:
            fail_task(
                claimed_task["id"],
                claimed_task["generation"],
                claimed_task["lease_token"],
                error,
            )
            raise
    if more_work:
        if queue_refresh_wake:
            wake_queue_refresh()
            return more_work
        try:
            celery_app.send_task("app.tasks.poll_background_tasks", queue="background")
        except BrokerUnavailable:
            _LOGGER.exception(
                "Could not wake background work; periodic polling will retry"
            )
    return more_work
