"""Stream ordered PostgreSQL rows for backup and upgrade verification."""

from __future__ import annotations

import hashlib

import psycopg
from psycopg import sql

FINGERPRINT_BATCH_SIZE = 1_000


def update_row_digest(digest: hashlib._Hash, row: tuple) -> None:
    for value in row:
        if value is None:
            digest.update(b"N")
            continue
        if isinstance(value, bytes):
            encoded = value
            kind = b"B"
        elif isinstance(value, int):
            encoded = str(value).encode("ascii")
            kind = b"I"
        elif isinstance(value, float):
            encoded = repr(value).encode("ascii")
            kind = b"F"
        else:
            encoded = str(value).encode("utf-8")
            kind = b"S"
        digest.update(kind)
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    digest.update(b"\n")


def destination_fingerprint(
    destination: psycopg.Connection,
    table_name: str,
    column_names: list[str],
    primary_key_columns: list[str],
    text_primary_key_columns: set[str],
) -> tuple[int, str]:
    order = sql.SQL(", ").join(
        sql.SQL('{} COLLATE "C"').format(sql.Identifier(column))
        if column in text_primary_key_columns else sql.Identifier(column)
        for column in primary_key_columns
    )
    projection = sql.SQL(", ").join(map(sql.Identifier, column_names))
    statement = sql.SQL("SELECT {} FROM {} ORDER BY {}").format(
        projection, sql.Identifier(table_name), order
    )
    digest = hashlib.sha256()
    count = 0
    with destination.transaction():
        with destination.cursor(name=f"verify_{table_name}") as cursor:
            cursor.itersize = FINGERPRINT_BATCH_SIZE
            cursor.execute(statement)
            for row in cursor:
                update_row_digest(digest, row)
                count += 1
    return count, digest.hexdigest()
