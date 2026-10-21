"""Existing deadline-bounded Psycopg connection; application owns the shared context."""
import time

from psycopg import Connection, OperationalError
from tempo_intelligence.runtime import background_deadline


class BackgroundConnection(Connection):
    """Bound client I/O as well as PostgreSQL's server-side transaction timeouts."""

    def wait(self, generator, interval=0.1, timeout=None):
        deadline = background_deadline.get()
        if deadline is None:
            return super().wait(generator, interval=interval, timeout=timeout)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            self.close()
            raise OperationalError("background_database_deadline") from None
        try:
            return super().wait(generator, interval=interval,
                                timeout=min(remaining, timeout) if timeout else remaining)
        except OperationalError:
            # A wait timeout is internal to Psycopg. Never return an incomplete
            # protocol exchange to the pool or expose its private diagnostics.
            self.close()
            raise OperationalError("background_database_unavailable") from None
