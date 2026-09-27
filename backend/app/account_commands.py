"""Foreground account-setting writes for the PostgreSQL product."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .postgres_store import PostgresConnection


def update_game_accounts(
    database: PostgresConnection, payload: dict[str, Any]
) -> dict[str, str]:
    lichess_username = str(payload["lichess_username"]).strip()
    chesscom_username = str(payload["chesscom_username"]).strip()
    settings_update = database.execute(
        "UPDATE settings SET lichess_username=?,chesscom_username=? WHERE id=1",
        (lichess_username, chesscom_username),
    )
    if settings_update.rowcount != 1:
        raise HTTPException(503, "Account settings are unavailable; retry after the database is restored")
    for provider, username in (
        ("lichess", lichess_username),
        ("chess.com", chesscom_username),
    ):
        if username:
            database.execute(
                "INSERT INTO game_accounts(provider,username) VALUES(?,?) "
                "ON CONFLICT(provider) DO UPDATE SET username=excluded.username",
                (provider, username),
            )
        else:
            database.execute("DELETE FROM game_accounts WHERE provider=?", (provider,))
    return {
        "lichess_username": lichess_username,
        "chesscom_username": chesscom_username,
    }


register_command("games.accounts.update", update_game_accounts)
