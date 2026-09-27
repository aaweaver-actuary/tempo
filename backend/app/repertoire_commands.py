"""Foreground repertoire writes for PostgreSQL."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .postgres_store import PostgresConnection


_SYSTEM_REPERTOIRES = ("__tactics__", "__endgames__", "__game_mistakes__")


def select_main_repertoire(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    repertoire_id = str(payload["repertoire_id"])
    # Every competing main-selection command locks the same eligible rows in
    # stable order before changing the single-main invariant.
    repertoire_ids = [row[0] for row in database.execute(
        "SELECT id FROM repertoires WHERE id NOT IN (?,?,?) ORDER BY id FOR UPDATE",
        _SYSTEM_REPERTOIRES,
    )]
    if repertoire_id not in repertoire_ids:
        raise HTTPException(404, "Repertoire not found")
    database.execute(
        "UPDATE repertoires SET is_main=CASE WHEN id=? THEN 1 ELSE 0 END "
        "WHERE id NOT IN (?,?,?)",
        (repertoire_id, *_SYSTEM_REPERTOIRES),
    )
    return {"id": repertoire_id, "is_main": True}


register_command("repertoires.main.select", select_main_repertoire)
