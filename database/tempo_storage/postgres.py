"""Native PostgreSQL execution, pooled connections and transaction ownership.

Statements use PostgreSQL syntax and psycopg ``%s`` parameters. No SQL dialect
translation, SQLite transaction emulation or application imports occur here.
"""

from __future__ import annotations

import atexit
from collections.abc import Iterator
from contextlib import contextmanager
import os
import threading
from typing import Any

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import RowMaker
from psycopg_pool import ConnectionPool


class TempoRow:
    """A query result with named fields and positional aggregate access."""

    def __init__(self, column_names: tuple[str, ...], values: tuple[Any, ...]):
        self._column_names = column_names
        self._values = values
        self._positions = {name: position for position, name in enumerate(column_names)}

    def __getitem__(self, key: str | int) -> Any:
        return self._values[key if isinstance(key, int) else self._positions[key]]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def keys(self) -> tuple[str, ...]:
        return self._column_names

    def __len__(self) -> int:
        return len(self._column_names)


def tempo_row_factory(cursor: psycopg.Cursor) -> RowMaker[TempoRow]:
    names = tuple(column.name for column in cursor.description or ())
    return lambda values: TempoRow(names, values)


class PostgresConnection:
    """A caller-owned PostgreSQL transaction with Tempo's concrete row shape."""

    def __init__(self, database: psycopg.Connection[TempoRow], *, read_only: bool = False):
        self._database = database
        self.read_only = read_only
        self._background_metric_deltas: dict[tuple, dict] = {}

    @property
    def raw(self) -> psycopg.Connection[TempoRow]:
        return self._database

    @property
    def in_transaction(self) -> bool:
        return self._database.info.transaction_status == TransactionStatus.INTRANS

    def execute(self, statement: str, parameters: tuple | list = ()) -> psycopg.Cursor[TempoRow]:
        return self._database.execute(statement, parameters)

    execute_native = execute

    def execute_native_batch(self, statements):
        with self._database.pipeline():
            cursors = [self.execute(statement, parameters) for statement, parameters in statements]
        return cursors

    def executemany(self, statement: str, parameters) -> psycopg.Cursor[TempoRow]:
        cursor = self._database.cursor()
        cursor.executemany(statement, parameters)
        return cursor

    def add_background_metrics(self, key: tuple, counts: dict) -> None:
        deltas = self._background_metric_deltas.setdefault(key, {})
        for name, amount in counts.items():
            deltas[name] = (max(deltas.get(name, 0), amount) if name.endswith("_max_seconds")
                            else deltas.get(name, 0) + amount)

    def flush_background_metrics(self) -> None:
        if self._background_metric_deltas:
            from tempo_storage.lifecycle.background_metrics import write_metric_delta
            for key, counts in sorted(self._background_metric_deltas.items()):
                write_metric_delta(self, key, counts)
            self._background_metric_deltas.clear()

    def commit(self) -> None:
        self.flush_background_metrics()
        self._database.commit()

    def rollback(self) -> None:
        self._background_metric_deltas.clear()
        self._database.rollback()


_pool_lock = threading.Lock()
_pools: dict[tuple[int, str, bool], ConnectionPool] = {}


def close_pools() -> None:
    with _pool_lock:
        pools = list(_pools.values())
        _pools.clear()
    for pool in pools:
        pool.close()


atexit.register(close_pools)


def configured() -> bool:
    return bool(os.getenv("TEMPO_DATABASE_WRITE_URL") or os.getenv("TEMPO_DATABASE_READ_URL"))


def _pool(read_only: bool, *, background: bool = False) -> ConnectionPool:
    name = "TEMPO_DATABASE_READ_URL" if read_only else "TEMPO_DATABASE_WRITE_URL"
    database_url = os.getenv(name)
    if not database_url:
        raise RuntimeError(f"{name} is missing")
    key = (os.getpid(), database_url, background)
    with _pool_lock:
        if key not in _pools:
            _pools[key] = ConnectionPool(
                conninfo=database_url,
                min_size=1,
                max_size=int(os.getenv("TEMPO_POSTGRES_BACKGROUND_POOL_SIZE", "1") if background
                             else os.getenv("TEMPO_POSTGRES_POOL_SIZE", "8")),
                kwargs={"row_factory": tempo_row_factory},
                open=True,
            )
        return _pools[key]


def _background_timeout_ms(name: str, default: int, *, minimum: int, maximum: int) -> int:
    value = int(os.getenv(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum} milliseconds")
    return value


def prepare_authoritative_background_pool() -> None:
    _pool(False, background=True).wait(timeout=5)


@contextmanager
def connection(*, read_only: bool = False, background: bool = False, authoritative: bool = False,
               repeatable_read: bool = False, pool_timeout_seconds: float | None = None,
               repertoire_snapshot: bool = False,
               queue_refresh_publication: bool = False) -> Iterator[PostgresConnection]:
    if queue_refresh_publication and (not background or read_only or repertoire_snapshot):
        raise ValueError("Queue refresh publication requires a background writer")
    if repertoire_snapshot and (not read_only or background):
        raise ValueError("Repertoire snapshot requires an unreserved read-only connection")
    options = ({"timeout": 0.1} if background else {}) if pool_timeout_seconds is None else {"timeout": pool_timeout_seconds}
    pool = _pool(read_only and not authoritative, background=background)
    with pool.connection(**options) as database:
        if repeatable_read:
            database.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        if read_only:
            database.execute("SET TRANSACTION READ ONLY")
        if repertoire_snapshot:
            database.execute("SELECT set_config('transaction_timeout','0',true), "
                             "set_config('lock_timeout','100ms',true), "
                             "set_config('statement_timeout','0',true)")
        elif background:
            transaction_limit = _background_timeout_ms(
                "TEMPO_POSTGRES_BACKGROUND_TRANSACTION_TIMEOUT_MS", 250,
                minimum=50, maximum=10000,
            )
            if queue_refresh_publication:
                transaction_limit = 500
            lock_limit = _background_timeout_ms(
                "TEMPO_POSTGRES_BACKGROUND_LOCK_TIMEOUT_MS", 25,
                minimum=1, maximum=1000,
            )
            database.execute("SELECT set_config('transaction_timeout', %s, true), "
                             "set_config('lock_timeout', %s, true), "
                             "set_config('jit', 'off', true)",
                             (f"{transaction_limit}ms", f"{lock_limit}ms"))
        transaction = PostgresConnection(database, read_only=read_only)
        yield transaction
        transaction.flush_background_metrics()


@contextmanager
def diagnostic_read_connection(timeout_seconds: float = 0.1):
    with connection(read_only=True, pool_timeout_seconds=timeout_seconds) as database:
        yield database
