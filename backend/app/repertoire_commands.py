"""Foreground repertoire writes for PostgreSQL."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .postgres_store import PostgresConnection
from .models import RepertoireSettingsRequest
from .repertoire_settings import repertoire_settings_response
from .queue_commands import request_queue_refresh_in_transaction
from .services.durable_tasks import enqueue_task_in_transaction


from .card_deletion import SYSTEM_REPERTOIRE_IDS as _SYSTEM_REPERTOIRES


def select_main_repertoire(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    repertoire_id = str(payload["repertoire_id"])
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        ("tempo:main-repertoire",),
    )
    # Every competing main-selection command locks the same eligible rows in
    # stable order before changing the single-main invariant.
    system_placeholders = ",".join("?" for _ in _SYSTEM_REPERTOIRES)
    repertoire_ids = [row[0] for row in database.execute(
        f"SELECT id FROM repertoires WHERE id NOT IN ({system_placeholders}) ORDER BY id FOR UPDATE",
        _SYSTEM_REPERTOIRES,
    )]
    if repertoire_id not in repertoire_ids:
        raise HTTPException(404, "Repertoire not found")
    database.execute(
        "UPDATE repertoires SET is_main=CASE WHEN id=? THEN 1 ELSE 0 END "
        f"WHERE id NOT IN ({system_placeholders})",
        (repertoire_id, *_SYSTEM_REPERTOIRES),
    )
    enqueue_task_in_transaction(database, "repertoire_game_refresh", "all", {"after_game_id": ""}, priority=90)
    return {"id": repertoire_id, "is_main": True}


def rename_repertoire(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    repertoire_id = str(payload["repertoire_id"])
    name = str(payload["name"]).strip()
    if not database.execute(
        "UPDATE repertoires SET name=? WHERE id=?", (name, repertoire_id),
    ).rowcount:
        raise HTTPException(404, "Repertoire not found")
    return {"id": repertoire_id, "name": name}


def delete_repertoire(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    from .card_deletion import delete_repertoire_data
    return delete_repertoire_data(database, str(payload["repertoire_id"]), str(payload.get("learned_cards", "delete")))


register_command("repertoires.main.select", select_main_repertoire)
register_command("repertoires.rename", rename_repertoire)
register_command("repertoires.delete", delete_repertoire)


def update_repertoire_settings(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    repertoire_id = str(payload["repertoire_id"])
    settings = RepertoireSettingsRequest.model_validate(payload["settings"])
    repertoire_settings_response(database, repertoire_id)
    database.execute("UPDATE repertoires SET new_cards_per_day=? WHERE id=?",
                     (settings.new_cards_per_day, repertoire_id))
    request_queue_refresh_in_transaction(database, date.today().isoformat())
    return repertoire_settings_response(database, repertoire_id)


register_command("repertoires.settings.update", update_repertoire_settings)
