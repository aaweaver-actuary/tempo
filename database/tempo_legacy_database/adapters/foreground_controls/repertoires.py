"""Repertoire identity/limit writes with the existing main-selection locks."""


def lock_main_selection(database, system_repertoires: tuple[str, ...]):
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        ("tempo:main-repertoire",),
    )
    # Every competing command locks the same eligible rows in stable order.
    system_placeholders = ",".join("?" for _ in system_repertoires)
    return [row[0] for row in database.execute(
        f"SELECT id FROM repertoires WHERE id NOT IN ({system_placeholders}) ORDER BY id FOR UPDATE",
        system_repertoires,
    )]


def store_main_selection(database, repertoire_id: str, system_repertoires: tuple[str, ...]) -> None:
    system_placeholders = ",".join("?" for _ in system_repertoires)
    database.execute(
        "UPDATE repertoires SET is_main=CASE WHEN id=? THEN 1 ELSE 0 END "
        f"WHERE id NOT IN ({system_placeholders})",
        (repertoire_id, *system_repertoires),
    )


def rename_repertoire(database, name: str, repertoire_id: str):
    return database.execute(
        "UPDATE repertoires SET name=? WHERE id=?", (name, repertoire_id),
    ).rowcount


def store_daily_limit(database, new_cards_per_day: int | None, repertoire_id: str) -> None:
    database.execute("UPDATE repertoires SET new_cards_per_day=? WHERE id=?",
                     (new_cards_per_day, repertoire_id))
