"""Celery entry points; task payloads carry named commands, never callables."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import date
from typing import Any

from kombu.exceptions import OperationalError as BrokerUnavailable
from psycopg.errors import LockNotAvailable, TransactionTimeout

from .celery_app import celery_app
from .command_gateway import execute_command
from .database import read_connection
from . import study_commands  # noqa: F401 - registers explicit worker commands
from . import study_attempt_commands  # noqa: F401 - registers Study attempt commands
from . import defense_commands  # noqa: F401 - registers defensive exercise commands
from . import tactic_commands  # noqa: F401 - registers tactic attempt commands
from . import queue_commands  # noqa: F401 - registers foreground queue commands
from . import review_commands  # noqa: F401 - registers foreground review command
from . import teaching_commands  # noqa: F401 - registers foreground teaching command
from . import repertoire_commands  # noqa: F401 - registers foreground repertoire command
from . import account_commands  # noqa: F401 - registers foreground account command
from . import settings_commands  # noqa: F401 - registers foreground settings command
from . import game_sync_commands  # noqa: F401 - registers foreground sync admission
from . import annotation_commands  # noqa: F401 - registers foreground position notes
from . import endgame_commands  # noqa: F401 - registers foreground endgame templates
from . import branch_commands  # noqa: F401 - registers foreground branch edits
from . import pgn_import_commands  # noqa: F401 - registers foreground PGN imports
from . import analysis_paste_commands  # noqa: F401 - registers foreground analysis paste
from . import prefix_split_commands  # noqa: F401 - registers foreground prefix splits
from . import integrity_repair_commands  # noqa: F401 - registers guided integrity repairs
from .services.activity_gate import activity_gate
from .services.durable_tasks import claim_task, complete_task, defer_task_for_contention, fail_task
from .services.priority_retention import execute_priority_retention_slice
from .services.postgres_queue_refresh import execute_postgres_queue_refresh_slice
from .services.repertoire_game_refresh import execute_repertoire_game_refresh_slice
from .services.threat_pipeline import execute_threat_report_audit
from .services.threat_training import execute_defense_rubric_audit_slice
from .services.postgres_game_sync import execute_game_sync_record_slice
from .services.postgres_game_sync_windows import execute_game_sync_window_slice
from .services.postgres_opening_graph import execute_postgres_opening_graph_slice
from .services.postgres_integrity import execute_postgres_integrity_slice


_LOGGER = logging.getLogger("tempo.tasks")
_SUPPORTED_BACKGROUND_KINDS = (
    "daily_queue",
    "defensive_rubric_audit",
    "repertoire_game_refresh",
    "defensive_threat_report_audit",
    "priority_retention",
    "game_sync_record",
    "game_sync_window",
    "opening_graph_rebuild",
    "integrity_scan",
)


@celery_app.task(name="app.tasks.ensure_daily_queue")
def ensure_daily_queue() -> bool:
    """Start the new day's queue even when the API stays up past midnight."""

    queue_date = date.today().isoformat()
    with activity_gate.foreground():
        with read_connection() as database:
            projection = database.execute(
                "SELECT state,refresh_pending FROM queue_projections WHERE queue_date=?",
                (queue_date,),
            ).fetchone()
        if projection and projection["state"] == "ready" and not projection["refresh_pending"]:
            return False
        result = execute_command(
            uuid.uuid4().hex, "queue.ensure_current", {"queue_date": queue_date},
        )
        if result is None:
            raise RuntimeError("Could not request today's queue refresh")
        return bool(result["refresh_pending"])


@celery_app.task(name="app.tasks.execute_foreground_command", bind=True)
def execute_foreground_command(
    self, operation_id: str, command_name: str, payload: dict[str, Any]
) -> Any:
    submitted_at = (self.request.headers or {}).get("submitted_at")
    queue_wait = max(0.0, time.time() - float(submitted_at)) if submitted_at else None
    started = time.perf_counter()
    with activity_gate.foreground():
        result = execute_command(operation_id, command_name, payload)
    _LOGGER.info(
        "foreground command=%s queue_wait_seconds=%s execution_seconds=%.3f",
        command_name, f"{queue_wait:.3f}" if queue_wait is not None else "unknown",
        time.perf_counter() - started,
    )
    return result


@celery_app.task(name="app.tasks.poll_background_tasks")
def poll_background_tasks() -> bool:
    """Admit at most one durable slice per poll; failed dispatch reclaims later."""

    claimed_task = claim_task(allowed_kinds=_SUPPORTED_BACKGROUND_KINDS)
    if claimed_task is None:
        return False
    celery_app.send_task(
        "app.tasks.execute_background_slice",
        args=[claimed_task],
        queue="background",
    )
    return True


@celery_app.task(name="app.tasks.execute_background_slice", bind=True)
def execute_background_slice(self, claimed_task: dict[str, Any]) -> bool:
    background_handlers = {
        "daily_queue": execute_postgres_queue_refresh_slice,
        "defensive_rubric_audit": execute_defense_rubric_audit_slice,
        "repertoire_game_refresh": execute_repertoire_game_refresh_slice,
        "defensive_threat_report_audit": execute_threat_report_audit,
        "priority_retention": execute_priority_retention_slice,
        "game_sync_record": execute_game_sync_record_slice,
        "game_sync_window": execute_game_sync_window_slice,
        "opening_graph_rebuild": execute_postgres_opening_graph_slice,
        "integrity_scan": execute_postgres_integrity_slice,
    }
    handler = background_handlers.get(claimed_task["kind"])
    if handler is None:
        raise ValueError(f"Unported background handler: {claimed_task['kind']}")
    with activity_gate.background_job(claimed_task["kind"], claimed_task["id"]):
        try:
            more_work = handler(claimed_task)
            if claimed_task["kind"] not in {
                "daily_queue", "game_sync_record", "game_sync_window", "opening_graph_rebuild",
                "integrity_scan",
            }:
                complete_task(
                    claimed_task["id"], claimed_task["generation"], claimed_task["lease_token"]
                )
        except (LockNotAvailable, TransactionTimeout):
            defer_task_for_contention(
                claimed_task["id"], claimed_task["generation"], claimed_task["lease_token"]
            )
            more_work = True
        except Exception as error:
            fail_task(
                claimed_task["id"], claimed_task["generation"], claimed_task["lease_token"], error
            )
            raise
    if more_work:
        try:
            celery_app.send_task("app.tasks.poll_background_tasks", queue="background")
        except BrokerUnavailable:
            _LOGGER.exception("Could not wake background work; periodic polling will retry")
    return more_work
