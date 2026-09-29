"""PostgreSQL connection and SQL compatibility seam for the cutover.

Domain services still contain SQLite-shaped SQL. Translate and cache each
statement until the service queries have been ported to native PostgreSQL.
Only this module owns PostgreSQL connections; callers keep short context
managed transactions and mapping/indexable rows.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
import atexit
import os
import re
import threading
import logging
import time
from typing import Any

import psycopg
from psycopg.rows import RowMaker
from psycopg_pool import ConnectionPool
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
    "tablebase_cache": (
        "INSERT INTO tablebase_cache(fen_key,response_json,fetched_at) VALUES(%s,%s,%s) "
        "ON CONFLICT(fen_key) DO UPDATE SET response_json=EXCLUDED.response_json, "
        "fetched_at=EXCLUDED.fetched_at"
    ),
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
    if stripped.upper() == "BEGIN IMMEDIATE":
        return None
    if re.match(r"^PRAGMA\b", stripped, re.IGNORECASE):
        raise ValueError(f"SQLite PRAGMA cannot run against PostgreSQL: {stripped[:80]}")
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
        stripped = re.sub(r"^INSERT\s+OR\s+IGNORE\b", "INSERT", stripped, count=1, flags=re.IGNORECASE)
    statements = sqlglot.transpile(stripped, read="sqlite", write="postgres")
    if len(statements) != 1:
        raise ValueError(f"Expected one SQL statement: {stripped[:100]}")
    translated = re.sub(r"\bLIMIT -1\b", "LIMIT ALL", statements[0], flags=re.IGNORECASE)
    # SQLGlot adds NULLS FIRST to conflict-target columns as though they were
    # ORDER BY expressions. PostgreSQL rejects null ordering in ON CONFLICT.
    translated = re.sub(
        r"\bON\s+CONFLICT\s*\(([^()]*)\)",
        lambda match: "ON CONFLICT(" + re.sub(
            r"\s+NULLS\s+(?:FIRST|LAST)\b", "", match.group(1), flags=re.IGNORECASE,
        ) + ")",
        translated,
        flags=re.IGNORECASE,
    )
    if ignore_insert and not re.search(r"\bON\s+CONFLICT\b", translated, re.IGNORECASE):
        translated += " ON CONFLICT DO NOTHING"
    return translated


class PostgresConnection:
    def __init__(self, database: psycopg.Connection[TempoRow]):
        self._database = database

    @property
    def raw(self) -> psycopg.Connection[TempoRow]:
        """Expose PostgreSQL primitives only to the command receipt boundary."""

        return self._database

    def execute_native(self, statement: str, parameters: tuple | list = ()) -> psycopg.Cursor[TempoRow]:
        """Run an internal PostgreSQL statement without SQLite dialect translation."""

        return self._database.execute(statement, parameters)

    def execute(self, statement: str, parameters: tuple | list = ()) -> psycopg.Cursor[TempoRow]:
        translated = postgres_sql(statement)
        if translated is None:
            # Existing claims use BEGIN IMMEDIATE; the enclosing context has a
            # transaction already. Claim queries will gain row locks separately.
            return self._database.execute("SELECT 1 WHERE FALSE")
        return self._database.execute(translated, parameters)

    def executemany(self, statement: str, parameters: list[tuple] | tuple[tuple, ...]) -> psycopg.Cursor[TempoRow]:
        translated = postgres_sql(statement)
        if translated is None:
            raise ValueError("Cannot executemany a transaction control statement")
        cursor = self._database.cursor()
        cursor.executemany(translated, parameters)
        return cursor

    def commit(self) -> None:
        self._database.commit()

    def rollback(self) -> None:
        self._database.rollback()


_pool_lock = threading.Lock()
_pools: dict[tuple[int, str], ConnectionPool] = {}
_logger = logging.getLogger("tempo.background.postgres")


def _background_timeout_ms(name: str, default: int, *, minimum: int, maximum: int) -> int:
    value = int(os.getenv(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum} milliseconds")
    return value


def close_pools() -> None:
    with _pool_lock:
        pools = list(_pools.values())
        _pools.clear()
    for pool in pools:
        pool.close()


atexit.register(close_pools)


def configured() -> bool:
    return bool(os.getenv("TEMPO_DATABASE_WRITE_URL") or os.getenv("TEMPO_DATABASE_READ_URL"))


def _pool(read_only: bool) -> ConnectionPool:
    environment_name = "TEMPO_DATABASE_READ_URL" if read_only else "TEMPO_DATABASE_WRITE_URL"
    database_url = os.getenv(environment_name)
    if not database_url:
        raise RuntimeError(f"{environment_name} is missing")
    pool_key = (os.getpid(), database_url)
    with _pool_lock:
        pool = _pools.get(pool_key)
        if pool is None:
            pool = ConnectionPool(
                conninfo=database_url,
                min_size=1,
                max_size=int(os.getenv("TEMPO_POSTGRES_POOL_SIZE", "8")),
                kwargs={"row_factory": tempo_row_factory},
                open=True,
            )
            _pools[pool_key] = pool
        return pool


@contextmanager
def connection(*, read_only: bool = False, background: bool = False) -> Iterator[PostgresConnection]:
    started_at = time.perf_counter()
    acquired_at = configured_at = handled_at = None
    try:
        with _pool(read_only).connection() as database:
            acquired_at = time.perf_counter()
            if background:
                transaction_limit = _background_timeout_ms(
                    "TEMPO_POSTGRES_BACKGROUND_TRANSACTION_TIMEOUT_MS", 250,
                    minimum=50, maximum=10000,
                )
                lock_limit = _background_timeout_ms(
                    "TEMPO_POSTGRES_BACKGROUND_LOCK_TIMEOUT_MS", 25,
                    minimum=1, maximum=1000,
                )
                database.execute("SELECT set_config('transaction_timeout', %s, true)",
                                 (f"{transaction_limit}ms",))
                database.execute("SELECT set_config('lock_timeout', %s, true)",
                                 (f"{lock_limit}ms",))
            configured_at = time.perf_counter()
            try:
                yield PostgresConnection(database)
            finally:
                handled_at = time.perf_counter()
    finally:
        if background and acquired_at is not None:
            finished_at = time.perf_counter()
            _logger.info(
                "background_postgres_connection acquisition_seconds=%.3f settings_seconds=%.3f "
                "handler_seconds=%.3f commit_cleanup_seconds=%.3f",
                acquired_at - started_at,
                (configured_at or finished_at) - acquired_at,
                (handled_at or finished_at) - (configured_at or finished_at),
                finished_at - (handled_at or finished_at),
            )
