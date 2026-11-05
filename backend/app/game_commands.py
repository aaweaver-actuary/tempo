"""Foreground game mutations committed with their derived-work intents."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from tempo_legacy_database.adapters.games import mutations as storage
from .services.durable_tasks import enqueue_task_in_transaction


def set_game_exclusion(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    game_id = str(payload["game_id"])
    excluded = bool(payload["excluded"])
    game = storage.lock_game(database, game_id)
    if game is None:
        raise HTTPException(404, "Game not found")
    storage.set_adaptive_exclusion(database, game_id, excluded)
    now = datetime.now(timezone.utc).isoformat()
    if excluded:
        storage.exclude_pending_findings(database, game_id, now)
    else:
        storage.restore_excluded_findings(database, game_id, now)
    derivation = storage.restart_derivation(database, game_id, now)
    enqueue_task_in_transaction(
        database, "game_derivation_positions", game_id,
        {"game_id": game_id, "derivation_version": derivation["derivation_version"],
         "cursor": 0}, priority=125,
    )
    return {"game_id": game_id, "excluded": excluded}


def register_commands(name: str | None = None) -> None:
    """Explicitly register the selected command or all commands for assembly."""
    if name is not None and name not in {'games.exclusion.set'}:
        raise ValueError(f"Unknown command: {name}")
    if name is None or name == 'games.exclusion.set':
        register_command('games.exclusion.set', set_game_exclusion)
