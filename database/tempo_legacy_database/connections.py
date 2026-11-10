"""PostgreSQL connection admission and read-only startup schema validation."""

import logging
from contextvars import ContextVar
import time
from contextlib import AbstractContextManager, contextmanager
from typing import Iterator

from tempo_legacy_database.infrastructure import database_admission, database_measurement
from tempo_legacy_database.capabilities import BorrowedTransaction, ReadConnection
from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.ddl.schema_version import POSTGRES_SCHEMA_VERSION


_LOGGER = logging.getLogger("tempo.background")
_query_only_request: ContextVar[bool] = ContextVar("tempo_query_only_request", default=False)


@contextmanager
def query_only_request() -> Iterator[None]:
    token = _query_only_request.set(True)
    try:
        yield
    finally:
        _query_only_request.reset(token)


@contextmanager
def _measured_background_database_section():
    with database_measurement(), database_admission().background_database_section():
        yield


@contextmanager
def connection(*, background: bool = False, queue_refresh_publication: bool = False) -> Iterator[BorrowedTransaction | ReadConnection]:
    if queue_refresh_publication and not background:
        raise ValueError("Queue refresh publication requires background admission")
    if _query_only_request.get():
        with read_connection() as database:
            yield database
        return
    database_admission().assert_foreground_connection_allowed(background)
    if background:
        gate_started = time.perf_counter()
        with _measured_background_database_section():
            gate_wait_seconds = time.perf_counter() - gate_started
            transaction_started = time.perf_counter()
            try:
                options = {"queue_refresh_publication": True} if queue_refresh_publication else {}
                with postgres_store.connection(read_only=False, background=True, **options) as database:
                    yield database
            finally:
                _LOGGER.info(
                    "postgres background section foreground_wait=%.3fs transaction=%.3fs",
                    gate_wait_seconds, time.perf_counter() - transaction_started,
                )
    else:
        with postgres_store.connection(read_only=False) as database:
            yield database


def foreground_connection() -> AbstractContextManager[BorrowedTransaction | ReadConnection]:
    return connection(background=False)


def background_connection() -> AbstractContextManager[BorrowedTransaction | ReadConnection]:
    return connection(background=True)


@contextmanager
def read_connection(*, repertoire_snapshot: bool = False) -> Iterator[ReadConnection]:
    options = {"repertoire_snapshot": True} if repertoire_snapshot else {}
    with postgres_store.connection(read_only=True, **options) as database:
        yield database


@contextmanager
def background_read_connection(*, authoritative: bool = False,
                               pool_timeout_seconds: float | None = None) -> Iterator[ReadConnection]:
    with _measured_background_database_section():
        options = {"authoritative": True} if authoritative else {}
        if pool_timeout_seconds is not None:
            options["pool_timeout_seconds"] = pool_timeout_seconds
        with postgres_store.connection(read_only=True, background=True, **options) as database:
            yield database


def card_columns(database: BorrowedTransaction) -> list[str]:
    """Return cards columns in storage order for identity-preserving copies."""
    return [row[0] for row in database.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=? ORDER BY ordinal_position",
        ("cards",),
    )]


def initialize() -> None:
    """Check the existing PostgreSQL schema; migrations are an explicit operator action."""
    with read_connection() as database:
        version = database.execute("SELECT MAX(version) FROM tempo_schema_migrations").fetchone()[0]
    if version != POSTGRES_SCHEMA_VERSION:
        raise RuntimeError(
            f"Unsupported PostgreSQL schema version: {version!r}; "
            f"expected {POSTGRES_SCHEMA_VERSION}. Stop writers, verify a backup, "
            "then apply the PostgreSQL schema upgrade in docs/STARTUP-BACKGROUND-RECOVERY.md"
        )
