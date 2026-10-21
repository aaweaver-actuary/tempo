"""Receipt-backed requests for PostgreSQL daily statistics refresh."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from tempo_legacy_database.adapters.commands import statistics as storage
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from .services.durable_tasks import enqueue_compact_postgres_task_in_transaction


def request_daily_statistics_refresh(
    database: PostgresConnection, payload: dict[str, Any],
) -> dict[str, str]:
    local_day = str(payload["local_day"])
    try:
        datetime.fromisoformat(local_day)
    except ValueError as error:
        raise HTTPException(422, "Invalid local day") from error
    now = datetime.now(timezone.utc).isoformat()
    storage.queue_daily_statistics(database, local_day, now)
    enqueue_compact_postgres_task_in_transaction(
        database, "daily_statistics", local_day, {"local_day": local_day}, priority=120,
    )
    return {"local_day": local_day, "status": "queued"}


register_command("statistics.daily.refresh", request_daily_statistics_refresh)
