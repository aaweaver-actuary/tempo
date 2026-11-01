"""Compose the existing SQLite compatibility coordinator and its handlers."""
from datetime import date

from tempo_legacy_database.connections import read_connection, background_read_connection
from tempo_legacy_database.adapters.database_executor import submit_background_write, submit_foreground_write
from tempo_legacy_database.adapters.http_composition import queue_maintenance as queue_maintenance_storage
from app.services import queue_application
from app.services.durable_tasks import enqueue_task
from app.services.game_sync_coordinator import coordinator, register_durable_task_handler, register_maintenance_handler


def enqueue_daily_queue_refresh(*, foreground: bool = True) -> dict:
    from app.queue_commands import current_study_day
    with read_connection() as database:
        queue_date = current_study_day(database)
    task = enqueue_task("daily_queue", "current", {"queue_date": queue_date}, priority=10, foreground=foreground)
    submit = submit_foreground_write if foreground else submit_background_write
    submit(lambda database: queue_maintenance_storage.mark_queue_projection_refreshing(database, queue_date),
           label=f"daily-queue-refreshing:{queue_date}")
    coordinator.wake()
    return task


def _execute_daily_queue_task(task: dict) -> None:
    queue_date = task["payload"].get("queue_date") or date.today().isoformat()
    try:
        submit_background_write(
            lambda database: queue_application.materialize_daily_queue(database, queue_date,
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
    from app.queue_commands import current_study_day
    with read_connection() as database:
        queue_date = current_study_day(database)
        projection = queue_maintenance_storage.queue_projection_state(database, queue_date)
    if projection is None:
        enqueue_daily_queue_refresh(foreground=False)


def _ensure_daily_defense_admission() -> None:
    from app.services.defensive_analysis import analysis_enabled
    from app.services.threat_training import enqueue_defense_admission
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
    from app.services.discovery_admission import enqueue_admission_intent
    with read_connection() as database:
        intent = queue_maintenance_storage.pending_discovery_admission(database)
    if intent:
        enqueue_admission_intent(intent["id"])


_registered = False


def compose_compatibility_coordinator():
    """Register the existing handlers once and return the existing singleton."""
    global _registered
    if _registered:
        return coordinator
    from app.services.repertoire_integrity import execute_durable_integrity_repair
    from app.services.opening_graph import execute_opening_graph_rebuild
    from app.services.prefix_transition_application import execute_application_slice
    from app.services.postgres_opening_segmentation import execute_segmentation_slice
    from app.services.canonical_prefix_preview import execute_prefix_preview_slice
    from app.services.repertoire_game_refresh import execute_repertoire_game_refresh_slice
    from app.services.threat_pipeline import (execute_threat_scan_slice, execute_threat_validation,
        execute_threat_report_audit, execute_threat_backfill_slice)
    from app.services.threat_training import execute_defense_admission_slice, execute_defense_rubric_audit_slice
    from app.services.discovery_admission import execute_admission_intent_slice, execute_recommendation_request_slice
    handlers = {
        "daily_queue": _execute_daily_queue_task, "integrity_repair": execute_durable_integrity_repair,
        "opening_graph_rebuild": execute_opening_graph_rebuild, "prefix_transition_application": execute_application_slice,
        "opening_segmentation": execute_segmentation_slice, "canonical_prefix_preview": execute_prefix_preview_slice,
        "repertoire_game_refresh": execute_repertoire_game_refresh_slice, "defensive_threat_scan": execute_threat_scan_slice,
        "defensive_threat_validate": execute_threat_validation, "defensive_threat_report_audit": execute_threat_report_audit,
        "defensive_threat_backfill": execute_threat_backfill_slice, "defensive_admission": execute_defense_admission_slice,
        "defensive_rubric_audit": execute_defense_rubric_audit_slice, "discovery_admission": execute_admission_intent_slice,
        "discovery_recommendation": execute_recommendation_request_slice,
    }
    for kind, handler in handlers.items():
        register_durable_task_handler(kind, handler)
    for handler in (_ensure_current_daily_queue, _ensure_daily_defense_admission, _ensure_discovery_admissions):
        register_maintenance_handler(handler)
    _registered = True
    return coordinator
