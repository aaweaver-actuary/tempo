"""Retained mixed worker assembly; concrete choices belong to composition.

The legacy coordinator still mixes Games and Study maintenance. This extraction
removes its accidental API-import requirement; it does not certify independent
lifetimes or authorize activating any unmerged feature.
"""
from datetime import date

from tempo_legacy_database.connections import background_read_connection, read_connection
from tempo_legacy_database.adapters.database_executor import submit_background_write, submit_foreground_write
from tempo_legacy_database.adapters.http_composition import queue_maintenance as queue_maintenance_storage
from app.services.queue_application import materialize_daily_queue
from app.services.durable_tasks import enqueue_task
from app.services.game_sync_coordinator import coordinator, register_durable_task_handler, register_maintenance_handler
from app.services.repertoire_integrity import execute_durable_integrity_repair
from app.services.opening_graph import execute_opening_graph_rebuild
from app.services.prefix_transition_application import execute_application_slice
from app.services.postgres_opening_segmentation import execute_segmentation_slice
from app.services.canonical_prefix_preview import execute_prefix_preview_slice
from app.services.repertoire_game_refresh import execute_repertoire_game_refresh_slice
from app.services.threat_pipeline import (
    execute_threat_scan_slice, execute_threat_validation, execute_threat_report_audit, execute_threat_backfill_slice,
)
from app.services.threat_training import (
    execute_defense_admission_slice, execute_defense_rubric_audit_slice, enqueue_defense_admission,
)
from app.services.discovery_admission import execute_admission_intent_slice, execute_recommendation_request_slice, enqueue_admission_intent

def _execute_daily_queue_task(task: dict) -> None:
    queue_date = task["payload"].get("queue_date") or date.today().isoformat()
    try:
        submit_background_write(
            lambda database: materialize_daily_queue(database, queue_date,
                preserve_through_entry_id=task["payload"].get("preserve_through_entry_id")),
            label=f"daily-queue:{queue_date}",
        )
    except Exception as error:
        sanitized_error = str(error)[:500]
        submit_background_write(
            lambda database: queue_maintenance_storage.mark_queue_projection_failed(database, queue_date, sanitized_error),
            label=f"daily-queue-failed:{queue_date}",
        )
        raise




def _ensure_current_daily_queue() -> None:
    queue_date = date.today().isoformat()
    with read_connection() as database:
        projection = queue_maintenance_storage.queue_projection_state(database, queue_date)
    if projection is None:
        enqueue_daily_queue_refresh(foreground=False)




def _ensure_daily_defense_admission() -> None:
    from app.services.defensive_analysis import analysis_enabled
    with background_read_connection(authoritative=True) as database:
        if not analysis_enabled(database):
            return
    today = date.today().isoformat()
    with read_connection() as database:
        existing = queue_maintenance_storage.existing_defense_admission(database, today)
        eligible = queue_maintenance_storage.eligible_defense_admission(database)
    if not existing and eligible:
        enqueue_defense_admission(background=True)




def _ensure_discovery_admissions() -> None:
    with read_connection() as database:
        intent = queue_maintenance_storage.pending_discovery_admission(database)
    if intent:
        enqueue_admission_intent(intent["id"])




def request_daily_queue_refresh(*, queue_date, foreground, enqueue, submit, mark_refreshing, wake):
    task = enqueue("daily_queue", "current", {"queue_date": queue_date}, priority=10, foreground=foreground)
    submit(lambda database: mark_refreshing(database, queue_date), label=f"daily-queue-refreshing:{queue_date}")
    wake()
    return task


def enqueue_daily_queue_refresh(*, foreground: bool = True) -> dict:
    return request_daily_queue_refresh(queue_date=date.today().isoformat(), foreground=foreground,
        enqueue=enqueue_task, submit=submit_foreground_write if foreground else submit_background_write,
        mark_refreshing=queue_maintenance_storage.mark_queue_projection_refreshing, wake=coordinator.wake)


_registered = False

def register_legacy_handlers() -> None:
    """Register the same fifteen task and three maintenance operations once."""
    global _registered
    if _registered:
        return
    register_durable_task_handler("daily_queue", _execute_daily_queue_task)
    register_durable_task_handler("integrity_repair", execute_durable_integrity_repair)
    register_durable_task_handler("opening_graph_rebuild", execute_opening_graph_rebuild)
    register_durable_task_handler("prefix_transition_application", execute_application_slice)
    register_durable_task_handler("opening_segmentation", execute_segmentation_slice)
    register_durable_task_handler("canonical_prefix_preview", execute_prefix_preview_slice)
    register_durable_task_handler("repertoire_game_refresh", execute_repertoire_game_refresh_slice)
    register_durable_task_handler("defensive_threat_scan", execute_threat_scan_slice)
    register_durable_task_handler("defensive_threat_validate", execute_threat_validation)
    register_durable_task_handler("defensive_threat_report_audit", execute_threat_report_audit)
    register_durable_task_handler("defensive_threat_backfill", execute_threat_backfill_slice)
    register_durable_task_handler("defensive_admission", execute_defense_admission_slice)
    register_durable_task_handler("defensive_rubric_audit", execute_defense_rubric_audit_slice)
    register_durable_task_handler("discovery_admission", execute_admission_intent_slice)
    register_durable_task_handler("discovery_recommendation", execute_recommendation_request_slice)
    register_maintenance_handler(_ensure_current_daily_queue)
    register_maintenance_handler(_ensure_daily_defense_admission)
    register_maintenance_handler(_ensure_discovery_admissions)
    _registered = True
