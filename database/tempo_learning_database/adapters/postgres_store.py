"""Learning-owned SQL compatibility adapter; no connection/commit authority."""

from __future__ import annotations
from collections.abc import Iterator
from functools import lru_cache
import re
from typing import Any
import psycopg
from psycopg.rows import RowMaker
import sqlglot


class TempoRow:
    """Match the key and positional lookup used by existing sqlite3.Row code."""

    def __init__(self, column_names: tuple[str, ...], values: tuple[Any, ...]):
        self._column_names = column_names
        self._values = values
        self._positions = {name: position for position, name in enumerate(column_names)}

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._positions[key]]

    def __iter__(self) -> Iterator[Any]:
        # sqlite3.Row iterates values; code also tuple-unpacks aggregate rows.
        return iter(self._values)

    def keys(self) -> tuple[str, ...]:
        # dict(row) detects keys() and builds the mapping by name.
        return self._column_names

    def __len__(self) -> int:
        return len(self._column_names)


def tempo_row_factory(cursor: psycopg.Cursor) -> RowMaker[TempoRow]:
    column_names = tuple(column.name for column in cursor.description or ())
    return lambda values: TempoRow(column_names, values)


_REPLACE_QUERIES = {
    "repertoire_integrity_source_runs": (
        "INSERT INTO repertoire_integrity_source_runs"
        "(run_id,source_offset,observations_json,invalid_json) VALUES(%s,%s,%s,%s) "
        "ON CONFLICT(run_id,source_offset) DO UPDATE SET "
        "observations_json=EXCLUDED.observations_json, invalid_json=EXCLUDED.invalid_json"
    ),
}


@lru_cache(maxsize=4096)
def postgres_sql(sqlite_statement: str) -> str | None:
    stripped = sqlite_statement.strip().rstrip(";")
    stripped = re.sub(
        r"\breal_game_instant\(([^()]*)\)",
        r"CAST(\1 AS TIMESTAMPTZ)",
        stripped,
        flags=re.IGNORECASE,
    )
    if stripped.upper() == "BEGIN IMMEDIATE":
        return None
    if re.match(r"^PRAGMA\b", stripped, re.IGNORECASE):
        raise ValueError(
            f"SQLite PRAGMA cannot run against PostgreSQL: {stripped[:80]}"
        )
    replace_match = re.match(
        r"^INSERT\s+OR\s+REPLACE\s+INTO\s+([a-zA-Z_][a-zA-Z_0-9]*)\b",
        stripped,
        re.IGNORECASE,
    )
    if replace_match:
        table_name = replace_match.group(1).lower()
        if table_name not in _REPLACE_QUERIES:
            raise ValueError(f"Unmapped SQLite REPLACE table: {table_name}")
        return _REPLACE_QUERIES[table_name]
    ignore_insert = bool(re.match(r"^INSERT\s+OR\s+IGNORE\b", stripped, re.IGNORECASE))
    if ignore_insert:
        stripped = re.sub(
            r"^INSERT\s+OR\s+IGNORE\b", "INSERT", stripped, count=1, flags=re.IGNORECASE
        )
    statements = sqlglot.transpile(stripped, read="sqlite", write="postgres")
    if len(statements) != 1:
        raise ValueError(f"Expected one SQL statement: {stripped[:100]}")
    translated = re.sub(
        r"\bLIMIT -1\b", "LIMIT ALL", statements[0], flags=re.IGNORECASE
    )
    # SQLGlot adds NULLS FIRST to conflict-target columns as though they were
    # ORDER BY expressions. PostgreSQL rejects null ordering in ON CONFLICT.
    translated = re.sub(
        r"\bON\s+CONFLICT\s*\(([^()]*)\)",
        lambda match: (
            "ON CONFLICT("
            + re.sub(
                r"\s+NULLS\s+(?:FIRST|LAST)\b",
                "",
                match.group(1),
                flags=re.IGNORECASE,
            )
            + ")"
        ),
        translated,
        flags=re.IGNORECASE,
    )
    if ignore_insert and not re.search(r"\bON\s+CONFLICT\b", translated, re.IGNORECASE):
        translated += " ON CONFLICT DO NOTHING"
    return translated


class PostgresConnection:
    def __init__(
        self, database: psycopg.Connection[TempoRow], *, read_only: bool = False
    ):
        self._database = database
        self.read_only = read_only
        self._background_metric_deltas: dict[tuple, dict] = {}

    @property
    def raw(self) -> psycopg.Connection[TempoRow]:
        """Expose PostgreSQL primitives only to the command receipt boundary."""

        return self._database

    def execute_native(
        self, statement: str, parameters: tuple | list = ()
    ) -> psycopg.Cursor[TempoRow]:
        """Run an internal PostgreSQL statement without SQLite dialect translation."""

        return self._database.execute(statement, parameters)

    def execute_native_batch(self, statements):
        """Fetch a caller-bounded native read set without per-query round trips."""
        with self._database.pipeline():
            cursors = [
                self.execute_native(statement, parameters)
                for statement, parameters in statements
            ]
        return cursors

    def execute(
        self, statement: str, parameters: tuple | list = ()
    ) -> psycopg.Cursor[TempoRow]:
        translated = postgres_sql(statement)
        if translated is None:
            # Existing claims use BEGIN IMMEDIATE; the enclosing context has a
            # transaction already. Claim queries will gain row locks separately.
            return self._database.execute("SELECT 1 WHERE FALSE")
        return self._database.execute(translated, parameters)

    def executemany(
        self, statement: str, parameters: list[tuple] | tuple[tuple, ...]
    ) -> psycopg.Cursor[TempoRow]:
        translated = postgres_sql(statement)
        if translated is None:
            raise ValueError("Cannot executemany a transaction control statement")
        cursor = self._database.cursor()
        cursor.executemany(translated, parameters)
        return cursor


def configured():
    return True


def connection(*, read_only=False, **kwargs):
    from tempo_learning_database.connections import connection as owned_connection

    return owned_connection(read_only=read_only, **kwargs)
