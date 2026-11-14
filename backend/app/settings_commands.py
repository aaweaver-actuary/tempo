"""Transactional foreground settings writes for PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from tempo_storage.foreground_controls import settings as storage
from tempo_storage.postgres import PostgresConnection

from app.contracts.transport.settings import Settings

from .command_gateway import register_descriptor
from .contracts.command_registry import CommandDescriptor
from .queue_commands import request_queue_refresh_in_transaction
from .services.study_calendar import capture_study_calendar

_SETTINGS_COLUMNS = tuple(Settings.model_fields)


@dataclass(frozen=True)
class SettingsRequest:
    settings: Settings
    supplied_fields: frozenset[str]


def decode_settings_request(payload: dict[str, Any]) -> SettingsRequest:
    settings = Settings.model_validate(payload["settings"])
    supplied_fields = frozenset(payload["supplied_fields"])
    return SettingsRequest(settings, supplied_fields)


def apply_settings(
    database: PostgresConnection, request: SettingsRequest
) -> dict[str, object]:
    settings = request.settings
    supplied_fields = request.supplied_fields
    unknown_fields = supplied_fields - set(_SETTINGS_COLUMNS)
    if unknown_fields:
        raise HTTPException(422, "Unknown settings field")
    existing = storage.locked_settings(database)
    if existing is None:
        raise HTTPException(
            503, "Settings are unavailable; retry after the database is restored"
        )
    values = settings.model_dump(mode="json")
    if "study_only_mode" not in supplied_fields:
        values["study_only_mode"] = bool(existing["study_only_mode"])
    storage.store_settings(
        database,
        {**values, "study_only_mode": int(values["study_only_mode"])},
        _SETTINGS_COLUMNS,
    )
    calendar = capture_study_calendar(database)
    request_queue_refresh_in_transaction(database, calendar.day.isoformat())
    return values


def update_settings(
    database: PostgresConnection, payload: dict[str, Any]
) -> dict[str, object]:
    """Compatibility entry point for existing direct callers."""
    return apply_settings(database, decode_settings_request(payload))


def _prepare_settings(request: SettingsRequest) -> SettingsRequest:
    return request


def _refresh_policy_after_commit(_result: dict[str, object]) -> None:
    from .services.study_priority import refresh_policy

    refresh_policy()  # Post-commit generation fences any older policy read.


SETTINGS_COMMAND: CommandDescriptor[
    PostgresConnection, SettingsRequest, SettingsRequest, dict[str, object]
] = CommandDescriptor(
    "settings.update",
    decode_settings_request,
    _prepare_settings,
    apply_settings,
    after_commit=_refresh_policy_after_commit,
)
register_descriptor(SETTINGS_COMMAND)
