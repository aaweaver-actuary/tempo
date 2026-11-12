"""Existing line import/branch persistence on the caller-owned transaction."""


def initial_depth(database):
    return database.execute(
        "SELECT initial_depth FROM settings WHERE id=1",
    ).fetchone()[0]
