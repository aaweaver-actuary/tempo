"""Global settings row locks/writes and existing provider-account persistence."""


def locked_settings(database):
    return database.execute(
        "SELECT * FROM settings WHERE id=1 FOR UPDATE"
    ).fetchone()


def store_settings(database, stored_values: dict, columns: tuple[str, ...]) -> None:
    database.execute(
        "UPDATE settings SET " + ",".join(f"{column_name}=%s" for column_name in columns)
        + " WHERE id=1",
        tuple(stored_values[column_name] for column_name in columns),
    )
