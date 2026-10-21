"""Foreground teaching-state writes for PostgreSQL."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from app.contracts.transport.study import TeachingStateRequest
from tempo_legacy_database.adapters.commands import teaching as storage
from tempo_legacy_database.adapters.postgres_store import PostgresConnection


def record_teaching_state(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    card_id = str(payload["card_id"])
    request = TeachingStateRequest.model_validate(payload["teaching_state"])
    if storage.active_card_for_update(database, card_id) is None:
        raise HTTPException(404, "Card not found")
    taught_at = datetime.now(timezone.utc).isoformat()
    stored_taught_at = storage.record_teaching_timestamp(database, card_id, request.revision, request.ply, taught_at)
    return {
        "cardId": card_id,
        "revision": request.revision,
        "ply": request.ply,
        "taughtAt": stored_taught_at,
    }


register_command("cards.teaching.record", record_teaching_state)
