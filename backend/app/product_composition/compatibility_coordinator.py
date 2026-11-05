"""Compose the existing SQLite compatibility coordinator and its handlers."""

from datetime import date

from tempo_legacy_database.connections import read_connection
from tempo_legacy_database.adapters.database_executor import submit_background_write
from tempo_legacy_database.adapters.http_composition import (
    queue_maintenance as queue_maintenance_storage,
)
from app.services import queue_application
from app.services.durable_tasks import enqueue_task


def enqueue_daily_queue_refresh(*, foreground: bool = True) -> dict:
    from app.queue_commands import current_study_day

    with read_connection() as database:
        queue_date = current_study_day(database)
    task = enqueue_task(
        "daily_queue",
        "current",
        {"queue_date": queue_date},
        priority=10,
        foreground=foreground,
    )
    compose_compatibility_coordinator().wake()
    return task


def _execute_daily_queue_task(task: dict) -> None:
    queue_date = task["payload"].get("queue_date") or date.today().isoformat()

    def publish(database):
        import json
        from tempo_legacy_database.adapters.durable_tasks import storage
        from tempo_legacy_database.adapters.queue_refresh import foreground
        from app.services.durable_tasks import complete_task_slice_in_transaction

        # SQLite's owning writer transaction serializes acceptance and complete
        # materialization. Use the durable payload, including a late preserve
        # pointer, rather than the delivery snapshot.
        current = storage.task_by_id(database, (task["id"],))
        if not (
            current
            and current["state"] == "leased"
            and current["generation"] == task["generation"]
            and current["lease_token"] == task["lease_token"]
        ):
            return
        payload = json.loads(current["payload_json"])
        epochs = foreground.lock_refresh_epochs(database, queue_date)
        epoch = (
            int(epochs[0])
            if epochs is not None
            else foreground.signal_queue_refresh_in_transaction(database, queue_date)
        )
        queue_application.materialize_daily_queue(
            database,
            queue_date,
            preserve_through_entry_id=payload.get("preserve_through_entry_id"),
        )
        if not foreground.publish_refresh_epoch_if_current(database, queue_date, epoch):
            raise RuntimeError("Compatibility queue publication lost its request epoch")
        if not complete_task_slice_in_transaction(database, task):
            raise RuntimeError("Compatibility queue publication lost its task lease")

    # The coordinator's existing failure handler owns fenced failure status.
    submit_background_write(publish, label=f"daily-queue:{queue_date}")


def _ensure_current_daily_queue() -> None:
    from app.queue_commands import current_study_day

    with read_connection() as database:
        queue_date = current_study_day(database)
        projection = queue_maintenance_storage.queue_projection_state(
            database, queue_date
        )
    if projection is None:
        enqueue_daily_queue_refresh(foreground=False)


_registered = False


def compose_compatibility_coordinator():
    """Register the existing handlers once and return the existing singleton."""
    global _registered
    from app.services.game_sync_coordinator import (
        coordinator,
        register_durable_task_handler,
        register_maintenance_handler,
    )

    if _registered:
        return coordinator
    from app.services.repertoire_integrity import execute_durable_integrity_repair
    from app.services.opening_graph import execute_opening_graph_rebuild
    from app.services.prefix_transition_application import execute_application_slice
    from app.services.postgres_opening_segmentation import execute_segmentation_slice
    from app.services.canonical_prefix_preview import execute_prefix_preview_slice
    from app.services.repertoire_game_refresh import (
        execute_repertoire_game_refresh_slice,
    )

    handlers = {
        "daily_queue": _execute_daily_queue_task,
        "integrity_repair": execute_durable_integrity_repair,
        "opening_graph_rebuild": execute_opening_graph_rebuild,
        "prefix_transition_application": execute_application_slice,
        "opening_segmentation": execute_segmentation_slice,
        "canonical_prefix_preview": execute_prefix_preview_slice,
        "repertoire_game_refresh": execute_repertoire_game_refresh_slice,
    }
    for kind, handler in handlers.items():
        register_durable_task_handler(kind, handler)
    for handler in (_ensure_current_daily_queue,):
        register_maintenance_handler(handler)
    _registered = True
    return coordinator
