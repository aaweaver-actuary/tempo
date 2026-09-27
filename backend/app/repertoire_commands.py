"""Foreground repertoire writes for PostgreSQL."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .postgres_store import PostgresConnection


_SYSTEM_REPERTOIRES = ("__tactics__", "__endgames__", "__game_mistakes__")


def select_main_repertoire(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    repertoire_id = str(payload["repertoire_id"])
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        ("tempo:main-repertoire",),
    )
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


def rename_repertoire(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    repertoire_id = str(payload["repertoire_id"])
    name = str(payload["name"]).strip()
    if not database.execute(
        "UPDATE repertoires SET name=? WHERE id=?", (name, repertoire_id),
    ).rowcount:
        raise HTTPException(404, "Repertoire not found")
    return {"id": repertoire_id, "name": name}


register_command("repertoires.main.select", select_main_repertoire)
register_command("repertoires.rename", rename_repertoire)
