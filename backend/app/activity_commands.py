"""Foreground PostgreSQL controls for durable background activity."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from tempo_legacy_database.adapters.commands import activity as storage
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from .services.background_activity import set_control_in_transaction
from .services.durable_tasks import serialize_task, update_queue_refresh_status_in_transaction


def control_activity(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, bool]:
    source = payload.get("source")
    work_id = payload.get("id")
    action = payload.get("action")
    if not all(isinstance(value, str) for value in (source, work_id, action)):
        raise HTTPException(422, "Invalid activity control")
    if not set_control_in_transaction(database, source, work_id, action):
        raise HTTPException(404, "Background activity not found or cannot be controlled")
    return {"ok": True}


register_command("activity.control", control_activity)


def retry_failed_task(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    task_id = str(payload["task_id"])
    failed = storage.failed_task_for_update(database, task_id)
    if failed is None:
        raise HTTPException(404, "Terminal task not found")
    from .contracts.command_registry import RETIRED_TASK_KINDS
    if failed["kind"] in RETIRED_TASK_KINDS:
        raise HTTPException(410, "Task retired")
    now = datetime.now(timezone.utc).isoformat()
    storage.reset_failed_task(database, task_id, now)
    if failed["kind"] == "daily_queue":
        from .services.queue_refresh_wakeup import mark_queue_refresh_requested
        mark_queue_refresh_requested()
        update_queue_refresh_status_in_transaction(
            database, failed["kind"], json.loads(failed["payload_json"]), state="refreshing",
        )
    storage.record_retry_activity(database, task_id, now)
    retried = storage.record_manual_retry(database, task_id, failed["generation"], now)
    return serialize_task(retried)


register_command("activity.task.retry", retry_failed_task)
