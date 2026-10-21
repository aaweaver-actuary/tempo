"""Account-setting rows; application normalization and refresh orchestration stay outside."""


def update_account_settings(database, lichess_username: str, chesscom_username: str) -> int:
    return database.execute(
        "UPDATE settings SET lichess_username=?,chesscom_username=? WHERE id=1",
        (lichess_username, chesscom_username),
    ).rowcount


def save_game_account(database, provider: str, username: str) -> None:
    database.execute(
        "INSERT INTO game_accounts(provider,username) VALUES(?,?) "
        "ON CONFLICT(provider) DO UPDATE SET username=excluded.username",
        (provider, username),
    )


def delete_game_account(database, provider: str) -> None:
    database.execute("DELETE FROM game_accounts WHERE provider=?", (provider,))
