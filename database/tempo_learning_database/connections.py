"""Only the runtime boundary acquires connections and owns commits."""

from contextlib import contextmanager
from contextvars import ContextVar
from psycopg_pool import ConnectionPool
from tempo_learning_database.adapters.postgres_store import PostgresConnection, tempo_row_factory
from tempo_learning_runtime.clock import workspace_timezone

current_database = ContextVar("learning_database")


class RuntimeRoleScopeError(RuntimeError):
    """The configured runtime credential is privileged or outside runtime scope."""


class WorkspaceBindingError(RuntimeError):
    """The configured workspace does not match the migrated database owner state."""


class Database:
    def __init__(self, configuration):
        self.configuration = configuration
        self.writer = ConnectionPool(
            configuration.database_url,
            min_size=0,
            max_size=configuration.pool_size,
            timeout=0.25,
            kwargs={"row_factory": tempo_row_factory, "connect_timeout": 2},
            open=True,
            check=ConnectionPool.check_connection,
        )
        self.reader = ConnectionPool(
            configuration.reader_url,
            min_size=0,
            max_size=configuration.pool_size,
            timeout=0.25,
            kwargs={"row_factory": tempo_row_factory, "connect_timeout": 2},
            open=True,
            check=ConnectionPool.check_connection,
        )
        self.maintenance = ConnectionPool(
            configuration.database_url,
            min_size=0,
            max_size=1,
            timeout=0.1,
            kwargs={"row_factory": tempo_row_factory, "connect_timeout": 2},
            open=True,
            check=ConnectionPool.check_connection,
        )

    @contextmanager
    def scope(self):
        token = current_database.set(self)
        calendar = workspace_timezone.set(self.configuration.timezone)
        try:
            yield self
        finally:
            workspace_timezone.reset(calendar)
            current_database.reset(token)

    @contextmanager
    def transaction(self, *, read_only=False, background=False, intake=False, **unused):
        if intake and (read_only or not background):
            raise ValueError("Evidence intake uses the bounded background writer pool")
        pool = (
            self.reader
            if read_only
            else self.maintenance
            if background
            else self.writer
        )
        with pool.connection() as raw:
            if read_only:
                raw.execute("SET TRANSACTION READ ONLY")
            for setting, value in [
                ("statement_timeout", "200ms" if background else "1500ms"),
                ("lock_timeout", "25ms" if background else "250ms"),
                (
                    "transaction_timeout",
                    "1000ms" if intake else "250ms" if background else "2500ms",
                ),
            ]:
                raw.execute("SELECT set_config(%s,%s,true)", (setting, value))
            raw.execute("SET LOCAL search_path=learning,pg_catalog")
            privileged = raw.execute(
                "SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolbypassrls OR has_schema_privilege(current_user,'learning','CREATE') FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
            if privileged is None or privileged[0]:
                raise RuntimeRoleScopeError(
                    "Learning startup requires a restricted runtime/reader role"
                )
            row = raw.execute(
                "SELECT instance.workspace_id,settings.timezone FROM instance JOIN settings ON settings.id=instance.id WHERE instance.id=1"
            ).fetchone()
            if (
                row is None
                or row[0] != self.configuration.workspace_id
                or row[1] != self.configuration.timezone
            ):
                raise WorkspaceBindingError(
                    "Learning database workspace does not match configuration"
                )
            yield PostgresConnection(raw, read_only=read_only)

    def close(self):
        for pool in (self.writer, self.reader, self.maintenance):
            pool.close()


def connection(*, read_only=False, background=False, **options):
    return current_database.get().transaction(
        read_only=read_only, background=background, **options
    )


def read_connection():
    return connection(read_only=True)
