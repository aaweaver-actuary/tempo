"""Foreground repertoire writes for PostgreSQL."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from tempo_legacy_database.adapters.foreground_controls import repertoires as storage
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from .models import RepertoireSettingsRequest
from .repertoire_settings import repertoire_settings_response
from .queue_commands import request_queue_refresh_in_transaction
from .services.durable_tasks import enqueue_task_in_transaction


from .card_deletion import SYSTEM_REPERTOIRE_IDS as _SYSTEM_REPERTOIRES


def select_main_repertoire(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    repertoire_id = str(payload["repertoire_id"])
    repertoire_ids = storage.lock_main_selection(database, _SYSTEM_REPERTOIRES)
    if repertoire_id not in repertoire_ids:
        raise HTTPException(404, "Repertoire not found")
    storage.store_main_selection(database, repertoire_id, _SYSTEM_REPERTOIRES)
    enqueue_task_in_transaction(database, "repertoire_game_refresh", "all", {"after_game_id": ""}, priority=90)
    return {"id": repertoire_id, "is_main": True}


def rename_repertoire(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    repertoire_id = str(payload["repertoire_id"])
    name = str(payload["name"]).strip()
    if not storage.rename_repertoire(database, name, repertoire_id):
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
    storage.store_daily_limit(database, settings.new_cards_per_day, repertoire_id)
    request_queue_refresh_in_transaction(database, date.today().isoformat())
    return repertoire_settings_response(database, repertoire_id)


register_command("repertoires.settings.update", update_repertoire_settings)
