"""Admitted PostgreSQL transaction scopes and startup schema validation."""

from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from typing import Iterator

from tempo_storage import postgres
from tempo_storage.ddl.schema_version import POSTGRES_SCHEMA_VERSION
from tempo_storage.infrastructure import database_admission, database_measurement
from tempo_storage.postgres import PostgresConnection


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
def connection(*, background: bool = False,
               queue_refresh_publication: bool = False) -> Iterator[PostgresConnection]:
    if queue_refresh_publication and not background:
        raise ValueError("Queue refresh publication requires background admission")
    if _query_only_request.get():
        with read_connection() as database:
            yield database
        return
    database_admission().assert_foreground_connection_allowed(background)
    if background:
        with _measured_background_database_section():
            with postgres.connection(background=True,
                                     queue_refresh_publication=queue_refresh_publication) as database:
                yield database
    else:
        with postgres.connection() as database:
            yield database


def foreground_connection() -> AbstractContextManager[PostgresConnection]:
    return connection()


def background_connection() -> AbstractContextManager[PostgresConnection]:
    return connection(background=True)


@contextmanager
def read_connection(*, repertoire_snapshot: bool = False) -> Iterator[PostgresConnection]:
    with postgres.connection(read_only=True, repertoire_snapshot=repertoire_snapshot) as database:
        yield database


@contextmanager
def background_read_connection(*, authoritative: bool = False,
                               pool_timeout_seconds: float | None = None) -> Iterator[PostgresConnection]:
    with _measured_background_database_section():
        with postgres.connection(read_only=True, background=True, authoritative=authoritative,
                                 pool_timeout_seconds=pool_timeout_seconds) as database:
            yield database


def card_columns(database: PostgresConnection) -> list[str]:
    return [row[0] for row in database.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position", ("cards",),
    )]


def initialize() -> None:
    with read_connection() as database:
        version = database.execute("SELECT MAX(version) FROM tempo_schema_migrations").fetchone()[0]
    if version != POSTGRES_SCHEMA_VERSION:
        raise RuntimeError(
            f"Unsupported PostgreSQL schema version: {version!r}; "
            f"expected {POSTGRES_SCHEMA_VERSION}. Stop writers, verify a backup, "
            "then apply the PostgreSQL schema upgrade in docs/STARTUP-BACKGROUND-RECOVERY.md"
        )
