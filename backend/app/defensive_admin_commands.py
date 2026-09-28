"""Receipt-backed admission of defensive audit and backfill tasks."""

from __future__ import annotations

from typing import Any

from .command_gateway import register_command
from .postgres_store import PostgresConnection
from .services.durable_tasks import enqueue_task_in_transaction


def request_defensive_audit(
    database: PostgresConnection, _payload: dict[str, Any],
) -> dict[str, str]:
    task = enqueue_task_in_transaction(
        database, "defensive_threat_report_audit", "saved-reports",
        {"cursor": ""}, priority=135,
    )
    return {"status": "queued", "task_id": task["id"]}


def request_defensive_backfill(
    database: PostgresConnection, _payload: dict[str, Any],
) -> dict[str, str]:
    task = enqueue_task_in_transaction(
        database, "defensive_threat_backfill", "analyzed-games",
        {"phase": "games", "cursor": ""}, priority=160,
    )
    return {"status": "queued", "task_id": task["id"]}


register_command("defensive.audit", request_defensive_audit)
register_command("defensive.backfill", request_defensive_backfill)
