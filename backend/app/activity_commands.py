"""Foreground PostgreSQL controls for durable background activity."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .postgres_store import PostgresConnection
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
    job = database.execute_native(
        "SELECT analysis_version,analysis_evidence_version,status,lease_id "
        "FROM game_analysis_jobs WHERE game_id=%s FOR UPDATE", (work_id,),
    ).fetchone()
    if (job is None or f"{job['analysis_version']}:{job['analysis_evidence_version']}" != payload["generation"]
            or job["status"] != "leased" or job["lease_id"] != payload["lease_id"]):
        raise HTTPException(409, "Analysis lease is no longer active")
    database.execute_native(
        "INSERT INTO background_activity(source,work_id,generation_key,phase,completed_units,total_units,updated_at) "
        "VALUES('game_analysis',%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(source,work_id) DO UPDATE SET generation_key=excluded.generation_key,"
        "phase=excluded.phase,completed_units=excluded.completed_units,"
        "total_units=excluded.total_units,updated_at=excluded.updated_at",
        (work_id, payload["generation"], payload["phase"], completed, total,
         datetime.now(timezone.utc).isoformat()),
    )
    return {"ok": True}


def retry_failed_task(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    task_id = str(payload["task_id"])
    failed = database.execute_native(
        "SELECT * FROM background_tasks WHERE id=%s AND state='failed' FOR UPDATE", (task_id,),
    ).fetchone()
    if failed is None:
        raise HTTPException(404, "Terminal task not found")
    now = datetime.now(timezone.utc).isoformat()
    database.execute_native(
        "UPDATE background_tasks SET state='queued',phase='queued',attempt_count=0,"
        "transaction_timeout_count=0,transaction_timeout_checkpoint=NULL,"
        "next_attempt_at=%s,lease_token=NULL,lease_expires_at=NULL,last_error=NULL,"
        "completed_at=NULL,updated_at=%s WHERE id=%s", (now, now, task_id),
    )
    if failed["kind"] == "daily_queue":
        update_queue_refresh_status_in_transaction(
            database, failed["kind"], json.loads(failed["payload_json"]), state="refreshing",
        )
    database.execute_native(
        "INSERT INTO background_activity(source,work_id,paused,phase,updated_at) "
        "VALUES('durable',%s,0,'Queued',%s) ON CONFLICT(source,work_id) DO UPDATE SET "
        "paused=0,phase='Queued',completed_units=NULL,total_units=NULL,updated_at=excluded.updated_at",
        (task_id, now),
    )
    database.execute_native(
        "INSERT INTO background_task_events(task_id,generation,event,phase,created_at) "
        "VALUES(%s,%s,'manual_retry','queued',%s)", (task_id, failed["generation"], now),
    )
    retried = database.execute_native(
        "SELECT * FROM background_tasks WHERE id=%s", (task_id,),
    ).fetchone()
    return serialize_task(retried)


register_command("activity.progress", report_analysis_progress)
register_command("activity.task.retry", retry_failed_task)
