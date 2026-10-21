"""Foreground PostgreSQL commands for endgame training templates."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from tempo_legacy_database.adapters.postgres_store import PostgresConnection


def create_endgame_template(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    raise HTTPException(410, "On-demand endgame calculation has been removed")


register_command("endgames.template.create", create_endgame_template)


def create_endgame_attempt(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    template_id = str(payload["template_id"])
    template = database.execute_native(
        "SELECT id FROM endgame_templates WHERE id=%s AND enabled=1 FOR UPDATE",
        (template_id,),
    ).fetchone()
    if template is None:
        raise HTTPException(404, "Endgame template not found")
    attempt_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    database.execute_native(
        "INSERT INTO endgame_attempts(id,template_id,start_fen,target,created_at) "
        "VALUES(%s,%s,%s,%s,%s)",
        (attempt_id, template_id, payload["fen"], payload["target"], created_at),
    )
    return {"id": attempt_id, "fen": payload["fen"],
            "target": payload["target"], "moves": payload["moves"]}


register_command("endgames.attempt.create", create_endgame_attempt)
