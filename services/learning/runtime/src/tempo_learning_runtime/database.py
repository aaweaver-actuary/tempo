"""Only the runtime boundary acquires connections and owns commits."""

from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock
from psycopg_pool import ConnectionPool
from .postgres_store import PostgresConnection, tempo_row_factory
from .clock import workspace_timezone

current_database = ContextVar("learning_database")
endgame_lane = ContextVar("learning_endgame_lane", default=False)


class Database:
    def __init__(self, configuration):
        self.configuration = configuration
        self._endgame_pools = {}
        self._endgame_pool_lock = Lock()
        self._endgame_closed = False
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

    def _endgame_pool(self, read_only):
        # Construct only on first use. Optional requests have one serial worker,
        # but the lock also protects direct adapter consumers and shutdown.
        with self._endgame_pool_lock:
            if self._endgame_closed:
                raise RuntimeError("Optional endgame pools are closed")
            if read_only not in self._endgame_pools:
                self._endgame_pools[read_only] = ConnectionPool(
                    self.configuration.reader_url if read_only else self.configuration.database_url,
                    min_size=0, max_size=1, timeout=0.25,
                    kwargs={"row_factory": tempo_row_factory, "connect_timeout": 2},
                    open=True, check=ConnectionPool.check_connection,
                )
            return self._endgame_pools[read_only]

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
        if endgame_lane.get() and background:
            raise RuntimeError("Optional endgame work cannot use maintenance capacity")
        if intake and (read_only or not background):
            raise ValueError("Evidence intake uses the bounded background writer pool")
        pool = (
            self._endgame_pool(read_only)
            if endgame_lane.get() and not background
            else self.reader
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
                raise RuntimeError(
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
                raise RuntimeError(
                    "Learning database workspace does not match configuration"
                )
            yield PostgresConnection(raw, read_only=read_only)

    def close_endgames(self):
        """Called only by the optional worker after its active phase leaves."""
        with self._endgame_pool_lock:
            self._endgame_closed = True
            pools = tuple(self._endgame_pools.values())
        for pool in pools:
            pool.close()

    def close(self):
        # Never acquire the optional lane lock or touch its active pools.
        for pool in (self.writer, self.reader, self.maintenance):
            pool.close()


def connection(*, read_only=False, background=False, **options):
    return current_database.get().transaction(
        read_only=read_only, background=background, **options
    )


def read_connection():
    return connection(read_only=True)
