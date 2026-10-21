"""Transactional foreground settings writes for PostgreSQL."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from tempo_legacy_database.adapters.foreground_controls import settings as storage
from .models import Settings
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from .queue_commands import request_queue_refresh_in_transaction


_SETTINGS_COLUMNS = tuple(Settings.model_fields)
_DEFERRED_REFRESH_COLUMNS = frozenset({
    "discovery_window_days", "coverage_reply_denominator",
    "coverage_cumulative_target", "coverage_horizon_fullmoves",
    "coverage_path_floor", "coverage_maia_elo",
})


def update_settings(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    settings = Settings.model_validate(payload["settings"])
    supplied_fields = set(payload["supplied_fields"])
    unknown_fields = supplied_fields - set(_SETTINGS_COLUMNS)
    if unknown_fields:
        raise HTTPException(422, "Unknown settings field")
    existing = storage.locked_settings(database)
    if existing is None:
        raise HTTPException(503, "Settings are unavailable; retry after the database is restored")
    if any(existing[column_name] != getattr(settings, column_name)
           for column_name in _DEFERRED_REFRESH_COLUMNS):
        raise HTTPException(503, "Coverage and discovery settings await the analysis-worker cutover")
    values = settings.model_dump(mode="json")
    for boolean_field in ("include_defensive_cards_in_daily_stack", "defensive_analysis_enabled", "study_only_mode"):
        if boolean_field not in supplied_fields:
            values[boolean_field] = bool(existing[boolean_field])
    stored_values = {**values,
        "include_defensive_cards_in_daily_stack": int(values["include_defensive_cards_in_daily_stack"]),
        "defensive_analysis_enabled": int(values["defensive_analysis_enabled"]),
        "study_only_mode": int(values["study_only_mode"]),
    }
    storage.store_settings(database, stored_values, _SETTINGS_COLUMNS)
    for provider, username in (
        ("lichess", settings.lichess_username.strip()),
        ("chess.com", settings.chesscom_username.strip()),
    ):
        if username:
            storage.store_provider_account(database, provider, username)
        else:
            storage.remove_provider_account(database, provider)
    from .services.postgres_next_opponent import request_profile_refresh
    if not values['study_only_mode']:
        request_profile_refresh(database)
    request_queue_refresh_in_transaction(database, date.today().isoformat())
    return {**values, "include_defensive_cards_in_daily_stack": bool(
        values["include_defensive_cards_in_daily_stack"]
    )}


register_command("settings.update", update_settings)
