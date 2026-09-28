"""Foreground teaching-state writes for PostgreSQL."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .models import TeachingStateRequest
from .postgres_store import PostgresConnection


def record_teaching_state(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    card_id = str(payload["card_id"])
    request = TeachingStateRequest.model_validate(payload["teaching_state"])
    if database.execute(
        "SELECT 1 FROM cards WHERE id=? AND archived=0 FOR UPDATE", (card_id,),
    ).fetchone() is None:
        raise HTTPException(404, "Card not found")
    taught_at = datetime.now(timezone.utc).isoformat()
    inserted_state = database.execute(
        "INSERT INTO teaching_states(card_id,revision,ply,taught_at) VALUES(?,?,?,?) "
        "ON CONFLICT(card_id,revision,ply) DO NOTHING RETURNING taught_at",
        (card_id, request.revision, request.ply, taught_at),
    ).fetchone()
    if inserted_state is None:
        inserted_state = database.execute(
            "SELECT taught_at FROM teaching_states WHERE card_id=? AND revision=? AND ply=?",
            (card_id, request.revision, request.ply),
        ).fetchone()
    return {
        "cardId": card_id,
        "revision": request.revision,
        "ply": request.ply,
        "taughtAt": inserted_state[0],
    }


register_command("cards.teaching.record", record_teaching_state)
