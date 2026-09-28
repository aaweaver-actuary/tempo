"""Foreground PostgreSQL controls for durable background activity."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .postgres_store import PostgresConnection
from .services.background_activity import set_control_in_transaction


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
