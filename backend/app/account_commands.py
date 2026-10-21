"""Foreground account-setting writes for the PostgreSQL product."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from tempo_legacy_database.adapters.commands import accounts as storage
from tempo_legacy_database.adapters.postgres_store import PostgresConnection


def update_game_accounts(
    database: PostgresConnection, payload: dict[str, Any]
) -> dict[str, str]:
    lichess_username = str(payload["lichess_username"]).strip()
    chesscom_username = str(payload["chesscom_username"]).strip()
    if storage.update_account_settings(database, lichess_username, chesscom_username) != 1:
        raise HTTPException(503, "Account settings are unavailable; retry after the database is restored")
    for provider, username in (
        ("lichess", lichess_username),
        ("chess.com", chesscom_username),
    ):
        if username:
            storage.save_game_account(database, provider, username)
        else:
            storage.delete_game_account(database, provider)
    from .services.postgres_next_opponent import request_profile_refresh
    request_profile_refresh(database)
    return {
        "lichess_username": lichess_username,
        "chesscom_username": chesscom_username,
    }


register_command("games.accounts.update", update_game_accounts)
