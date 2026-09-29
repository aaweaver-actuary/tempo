"""Exercise background timeout scope and rollback on a disposable PostgreSQL stack."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from psycopg.errors import TransactionTimeout

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import postgres_store


def main() -> None:
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("This check requires the disposable PostgreSQL test instance")
    os.environ["TEMPO_DATABASE_WRITE_URL"] = "postgresql://postgres@postgres:5432/tempo"
    os.environ["TEMPO_POSTGRES_POOL_SIZE"] = "1"
    os.environ["TEMPO_POSTGRES_BACKGROUND_TRANSACTION_TIMEOUT_MS"] = "1000"
    os.environ["TEMPO_POSTGRES_BACKGROUND_LOCK_TIMEOUT_MS"] = "25"
    with postgres_store.connection(background=True) as database:
        assert database.raw.execute("SHOW transaction_timeout").fetchone()[0] == "1s"
        assert database.raw.execute("SHOW lock_timeout").fetchone()[0] == "25ms"
        assert database.raw.execute("SELECT pg_sleep(0.075)").fetchone() is not None
    with postgres_store.connection() as database:
        assert database.raw.execute("SHOW transaction_timeout").fetchone()[0] == "0"
        assert database.raw.execute("SHOW lock_timeout").fetchone()[0] == "0"
    os.environ["TEMPO_POSTGRES_BACKGROUND_TRANSACTION_TIMEOUT_MS"] = "50"
    try:
        with postgres_store.connection(background=True) as database:
            database.raw.execute("SELECT pg_sleep(0.075)")
    except TransactionTimeout:
        pass
    else:
        raise AssertionError("The disposable transaction did not time out")
    with postgres_store.connection() as database:
        assert database.raw.execute("SELECT 1").fetchone()[0] == 1
        assert database.raw.execute("SHOW transaction_timeout").fetchone()[0] == "0"
    postgres_store.close_pools()
    print("PASS background PostgreSQL settings are local, bounded, and recover after rollback")


if __name__ == "__main__":
    main()
