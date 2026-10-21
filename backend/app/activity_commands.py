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


def report_analysis_progress(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, bool]:
    if payload.get("source") != "game_analysis" or not all(
        isinstance(payload.get(field), str)
        for field in ("id", "generation", "phase", "lease_id")
    ):
        raise HTTPException(422, "Invalid analysis progress")
    completed, total = payload.get("completed"), payload.get("total")
    if (isinstance(completed, bool) or not isinstance(completed, int)
            or isinstance(total, bool) or not isinstance(total, int)
            or completed < 0 or total < completed):
        raise HTTPException(422, "Invalid analysis progress count")
    work_id = payload["id"]
    job = storage.analysis_lease_for_update(database, work_id)
    if (job is None or f"{job['analysis_version']}:{job['analysis_evidence_version']}" != payload["generation"]
            or job["status"] != "leased" or job["lease_id"] != payload["lease_id"]):
        raise HTTPException(409, "Analysis lease is no longer active")
    storage.save_analysis_progress(
        database, work_id, payload["generation"], payload["phase"], completed, total,
        datetime.now(timezone.utc).isoformat(),
    )
    return {"ok": True}


def retry_failed_task(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    task_id = str(payload["task_id"])
    failed = storage.failed_task_for_update(database, task_id)
    if failed is None:
        raise HTTPException(404, "Terminal task not found")
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


register_command("activity.progress", report_analysis_progress)
register_command("activity.task.retry", retry_failed_task)
