"""Celery entry points; task payloads carry named commands, never callables."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import date
from typing import Any

from kombu.exceptions import OperationalError as BrokerUnavailable
import psycopg
from psycopg.errors import DeadlockDetected, LockNotAvailable, SerializationFailure, TransactionTimeout

from .celery_app import celery_app
from .command_gateway import (
    claim_recoverable_operation, execute_command, record_operation_attempt,
    record_operation_retry,
)
from .database import read_connection
from . import study_commands  # noqa: F401 - registers explicit worker commands
from . import study_attempt_commands  # noqa: F401 - registers Study attempt commands
from . import defense_commands  # noqa: F401 - registers defensive exercise commands
from . import tactic_commands  # noqa: F401 - registers tactic attempt commands
from . import queue_commands  # noqa: F401 - registers foreground queue commands
from . import review_commands  # noqa: F401 - registers foreground review command
from . import teaching_commands  # noqa: F401 - registers foreground teaching command
from . import repertoire_commands  # noqa: F401 - registers foreground repertoire command
from . import canonical_prefix_api  # noqa: F401 - registers canonical opening commands
from . import account_commands  # noqa: F401 - registers foreground account command
from . import settings_commands  # noqa: F401 - registers foreground settings command
from . import game_sync_commands  # noqa: F401 - registers foreground sync admission
from . import game_commands  # noqa: F401 - registers foreground game changes
from . import guided_review_commands  # noqa: F401 - registers foreground guided review changes
from . import finding_commands  # noqa: F401 - registers foreground finding decisions
from . import finding_card_commands  # noqa: F401 - registers foreground finding cards
from . import annotation_commands  # noqa: F401 - registers foreground position notes
from . import endgame_commands  # noqa: F401 - registers foreground endgame templates
from . import branch_commands  # noqa: F401 - registers foreground branch edits
from . import pgn_import_commands  # noqa: F401 - registers foreground PGN imports
from . import analysis_paste_commands  # noqa: F401 - registers foreground analysis paste
from . import prefix_split_commands  # noqa: F401 - registers foreground prefix splits
from . import card_commands  # noqa: F401 - registers foreground card revisions
from . import opportunity_commands  # noqa: F401 - registers foreground discovery state changes
from . import activity_commands  # noqa: F401 - registers foreground activity controls
from . import statistics_commands  # noqa: F401 - registers foreground statistics refresh
from . import defensive_admin_commands  # noqa: F401 - registers defensive admin tasks
from . import threat_analysis_commands  # noqa: F401 - registers defensive engine callbacks
from . import coverage_commands  # noqa: F401 - registers foreground coverage admission
from . import coverage_maia_commands  # noqa: F401 - registers background Maia callbacks
from . import game_analysis_commands  # noqa: F401 - registers background game analysis claims
from . import game_analysis_publication  # noqa: F401 - registers analysis publication admission
from . import discovery_commands  # noqa: F401 - registers discovery acceptance
from .game_analysis_publication import (
    execute_game_analysis_followup_slice, execute_game_analysis_publication_slice,
)
from . import integrity_repair_commands  # noqa: F401 - registers guided integrity repairs
from .services.activity_gate import activity_gate
from .services.durable_tasks import claim_task, complete_task, defer_task_for_contention, fail_task
from .services.priority_retention import execute_priority_retention_slice
from .services.postgres_queue_refresh import execute_postgres_queue_refresh_slice
from .services.repertoire_game_refresh import execute_repertoire_game_refresh_slice
from .services.threat_pipeline import (
    execute_threat_backfill_slice, execute_threat_report_audit,
    execute_threat_scan_slice, execute_threat_validation,
)
from .services.threat_training import (
    execute_defense_admission_slice, execute_defense_rubric_audit_slice,
)
from .services.postgres_game_sync import execute_game_sync_record_slice
from .services.postgres_game_derivation import execute_game_position_index_slice
from .services.postgres_game_repertoire import execute_game_repertoire_comparison_slice
from .services.postgres_game_findings import execute_game_findings_slice
from .services.postgres_game_misses import execute_game_miss_slice
from .services.postgres_game_events import execute_game_event_slice
from .services.postgres_game_features import execute_game_feature_slice
from .services.postgres_game_priorities import execute_game_priority_handoff_slice
from .services.postgres_priority import execute_repertoire_priority_slice
from .services.postgres_daily_statistics import execute_postgres_daily_statistics_slice
from .services.postgres_game_sync_windows import execute_game_sync_window_slice
from .services.postgres_opening_graph import execute_postgres_opening_graph_slice
from .services.postgres_integrity import execute_postgres_integrity_slice
from .services.postgres_opening_segmentation import execute_segmentation_slice
from .services.repertoire_opportunities import execute_opportunity_slice
from .services.discovery_admission import (
    execute_admission_intent_slice, execute_recommendation_request_slice,
)
from .services.postgres_coverage_seed import execute_coverage_seed_slice
from .services.canonical_prefix_preview import execute_prefix_preview_slice
from .services.postgres_coverage_explorer import execute_coverage_explorer_slice
from .services.postgres_coverage_recovery import recover_one_explorer_run


_LOGGER = logging.getLogger("tempo.tasks")
_SUPPORTED_BACKGROUND_KINDS = (
    "daily_queue",
    "defensive_rubric_audit",
    "defensive_admission",
    "repertoire_game_refresh",
    "defensive_threat_report_audit",
    "defensive_threat_scan",
    "defensive_threat_backfill",
    "defensive_threat_validate",
    "priority_retention",
    "game_sync_record",
    "game_derivation_positions",
    "game_derivation_compare",
    "game_derivation_findings",
    "game_derivation_misses",
    "game_derivation_events",
    "game_derivation_features",
    "game_derivation_priorities",
    "repertoire_priority",
    "daily_statistics",
    "game_sync_window",
    "opening_graph_rebuild",
    "integrity_scan",
    "opening_segmentation",
    "repertoire_opportunity",
    "discovery_recommendation",
    "discovery_admission",
    "coverage_seed",
    "canonical_prefix_preview",
    "coverage_explorer",
    "game_analysis_publish",
    "game_analysis_followup",
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
    self, operation_id: str, command_name: str, payload: dict[str, Any],
    expected_retry_cycle: int | None = None,
) -> Any:
    submitted_at = (self.request.headers or {}).get("submitted_at")
    queue_wait = max(0.0, time.time() - float(submitted_at)) if submitted_at else None
    started = time.perf_counter()
    with activity_gate.foreground():
        should_execute, saved_payload, attempt_token, _attempt_number = record_operation_attempt(
            operation_id, command_name, payload, background=False,
            expected_retry_cycle=expected_retry_cycle,
        )
        if not should_execute:
            return None
        try:
            result = execute_command(operation_id, command_name, saved_payload,
                                     attempt_token=attempt_token)
        except Exception as error:
            record_operation_retry(operation_id, attempt_token, error,
                                   retryable=False, background=False)
            raise
    _LOGGER.info(
        "foreground command=%s queue_wait_seconds=%s execution_seconds=%.3f",
        command_name, f"{queue_wait:.3f}" if queue_wait is not None else "unknown",
        time.perf_counter() - started,
    )
    return result


@celery_app.task(name="app.tasks.execute_background_command", bind=True)
def execute_background_command(
    self, operation_id: str, command_name: str, payload: dict[str, Any],
    expected_retry_cycle: int | None = None,
) -> Any:
    """Execute an external worker callback in a bounded background section."""

    with activity_gate.background_job(command_name, operation_id):
        should_execute, saved_payload, attempt_token, attempt_number = record_operation_attempt(
            operation_id, command_name, payload, background=True,
            expected_retry_cycle=expected_retry_cycle,
        )
        if not should_execute:
            return None
        try:
            return execute_command(operation_id, command_name, saved_payload,
                                   background=True, attempt_token=attempt_token)
        except (psycopg.OperationalError, psycopg.InterfaceError,
                DeadlockDetected, LockNotAvailable, SerializationFailure, TransactionTimeout) as error:
            exhausted, delay_seconds = record_operation_retry(
                operation_id, attempt_token, error, retryable=True, background=True,
            )
            _LOGGER.warning("background command=%s operation_id=%s attempt=%s stage=execute "
                            "error_class=%s next_retry_seconds=%s", command_name, operation_id,
                            attempt_number, type(error).__name__, delay_seconds)
            return None
        except Exception as error:
            record_operation_retry(operation_id, attempt_token, error,
                                   retryable=False, background=True)
            raise


@celery_app.task(name="app.tasks.recover_operations")
def recover_operations() -> bool:
    with activity_gate.background_job("operation_recovery", "one-run"):
        operation = claim_recoverable_operation()
    if operation is None:
        return False
    task_is_background = operation["background"]
    celery_app.send_task(
        "app.tasks.execute_background_command" if task_is_background
        else "app.tasks.execute_foreground_command",
        args=[operation["operation_id"], operation["command_name"], operation["payload"]],
        task_id=operation["operation_id"],
        queue="background" if task_is_background else "foreground",
        headers={"submitted_at": time.time()},
    )
    return True


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


@celery_app.task(name="app.tasks.recover_active_coverage")
def recover_active_coverage() -> bool:
    with activity_gate.background_job("coverage_recovery", "one-run"):
        return recover_one_explorer_run()


@celery_app.task(name="app.tasks.execute_background_slice", bind=True)
def execute_background_slice(self, claimed_task: dict[str, Any]) -> bool:
    background_handlers = {
        "daily_queue": execute_postgres_queue_refresh_slice,
        "defensive_rubric_audit": execute_defense_rubric_audit_slice,
        "defensive_admission": execute_defense_admission_slice,
        "repertoire_game_refresh": execute_repertoire_game_refresh_slice,
        "defensive_threat_report_audit": execute_threat_report_audit,
        "defensive_threat_scan": execute_threat_scan_slice,
        "defensive_threat_backfill": execute_threat_backfill_slice,
        "defensive_threat_validate": execute_threat_validation,
        "priority_retention": execute_priority_retention_slice,
        "game_sync_record": execute_game_sync_record_slice,
        "game_derivation_positions": execute_game_position_index_slice,
        "game_derivation_compare": execute_game_repertoire_comparison_slice,
        "game_derivation_findings": execute_game_findings_slice,
        "game_derivation_misses": execute_game_miss_slice,
        "game_derivation_events": execute_game_event_slice,
        "game_derivation_features": execute_game_feature_slice,
        "game_derivation_priorities": execute_game_priority_handoff_slice,
        "repertoire_priority": execute_repertoire_priority_slice,
        "daily_statistics": execute_postgres_daily_statistics_slice,
        "game_sync_window": execute_game_sync_window_slice,
        "opening_graph_rebuild": execute_postgres_opening_graph_slice,
        "integrity_scan": execute_postgres_integrity_slice,
        "opening_segmentation": execute_segmentation_slice,
        "repertoire_opportunity": execute_opportunity_slice,
        "discovery_recommendation": execute_recommendation_request_slice,
        "discovery_admission": execute_admission_intent_slice,
        "coverage_seed": execute_coverage_seed_slice,
        "canonical_prefix_preview": execute_prefix_preview_slice,
        "coverage_explorer": execute_coverage_explorer_slice,
        "game_analysis_publish": execute_game_analysis_publication_slice,
        "game_analysis_followup": execute_game_analysis_followup_slice,
    }
    handler = background_handlers.get(claimed_task["kind"])
    if handler is None:
        raise ValueError(f"Unported background handler: {claimed_task['kind']}")
    with activity_gate.background_job(claimed_task["kind"], claimed_task["id"]):
        try:
            more_work = handler(claimed_task)
            if claimed_task["kind"] not in {
                "daily_queue", "game_sync_record", "game_sync_window", "game_derivation_positions",
                "game_derivation_compare", "game_derivation_findings",
                "game_derivation_misses",
                "game_derivation_events",
                "game_derivation_features", "game_derivation_priorities",
                "repertoire_priority",
                "daily_statistics",
                "opening_graph_rebuild",
                "integrity_scan",
                "opening_segmentation",
                "coverage_seed",
                "canonical_prefix_preview",
                "coverage_explorer",
                "game_analysis_publish",
                "game_analysis_followup",
                "defensive_threat_validate",
                "defensive_threat_backfill",
            }:
                complete_task(
                    claimed_task["id"], claimed_task["generation"], claimed_task["lease_token"]
                )
        except (DeadlockDetected, LockNotAvailable, SerializationFailure, TransactionTimeout):
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

from . import opening_segmentation_api  # noqa: F401 - registers advisory commands
