"""Exercise background timeout scope and rollback on a disposable PostgreSQL stack."""

from __future__ import annotations

# Preserve direct-script imports after relocation from scripts/.
import sys
from pathlib import Path
_REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPOSITORY))
sys.path.insert(0, str(_REPOSITORY / "database"))
sys.path.insert(0, str(_REPOSITORY / "backend"))

import os
import sys
import time
import uuid
from pathlib import Path

from psycopg import sql
from psycopg.errors import TransactionTimeout

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from tempo_legacy_database.adapters import postgres_store


def check_background_jit_admission() -> dict:
    """Real bounded writer, compiler counterfactual, rollback and pooled scope.

    Scalar-subquery bounds have conservative planner cardinality but return one
    row. The resulting many-subplan query crosses the ordinary JIT threshold
    without expensive data execution, like the observed queue classification.
    No production query or planner cost/compilation threshold is modified.
    """
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("JIT proof requires the disposable PostgreSQL test instance")
    branches = 8
    query = " UNION ALL ".join(
        f"SELECT {index} AS ordinal,sum(a.value+b.value+{index}) AS total "
        "FROM generate_series(1,(SELECT 1)) a(value) "
        "CROSS JOIN generate_series(1,(SELECT 1)) b(value)"
        for index in range(branches)) + " ORDER BY ordinal"
    expected = [(index, index + 2) for index in range(branches)]
    table = sql.Identifier("tempo_bg_jit_" + uuid.uuid4().hex)
    timeout_keys = ("TEMPO_POSTGRES_BACKGROUND_TRANSACTION_TIMEOUT_MS", "TEMPO_POSTGRES_BACKGROUND_LOCK_TIMEOUT_MS")
    previous = {key: os.environ.get(key) for key in timeout_keys}
    os.environ[timeout_keys[0]], os.environ[timeout_keys[1]] = "250", "25"
    try:
        postgres_store.prepare_authoritative_background_pool()
        pool = postgres_store._pool(False, background=True)
        with pool.connection(timeout=.1) as raw:
            original = tuple(raw.execute("SELECT current_setting('jit'),current_setting('transaction_timeout'),current_setting('lock_timeout')").fetchone())
        with postgres_store.connection(read_only=True, repertoire_snapshot=True) as database:
            reader_jit = database.raw.execute('SHOW jit').fetchone()[0]
        with postgres_store.connection() as database:
            foreground_jit = database.raw.execute('SHOW jit').fetchone()[0]
            database.raw.execute(sql.SQL('CREATE TABLE {} (marker integer)').format(table))
            database.raw.execute("SELECT set_config('jit','on',true)")
            plan = database.raw.execute('EXPLAIN (FORMAT JSON) ' + query).fetchone()[0][0]
            threshold = float(database.raw.execute('SHOW jit_above_cost').fetchone()[0])
            optimization_threshold = float(database.raw.execute('SHOW jit_optimize_above_cost').fetchone()[0])
            assert threshold < plan['Plan']['Total Cost'] < optimization_threshold
            assert plan.get('JIT', {}).get('Options', {}).get('Expressions') is True
            assert plan['JIT']['Options']['Optimization'] is False
        # Restore only compiler admission as the released writer did. The same
        # server-enforced 250ms/25ms profile must roll back the pending write.
        try:
            with postgres_store.connection(background=True) as database:
                assert database.raw.execute('SHOW transaction_timeout').fetchone()[0] == '250ms'
                assert database.raw.execute('SHOW lock_timeout').fetchone()[0] == '25ms'
                database.raw.execute("SELECT set_config('jit','on',true)")
                database.raw.execute(sql.SQL('INSERT INTO {} VALUES (1)').format(table))
                database.raw.execute(query).fetchall()
        except TransactionTimeout:
            pass
        else:
            raise AssertionError('Controlled high-cost JIT query did not reproduce the short-writer timeout')
        postgres_store.prepare_authoritative_background_pool()
        with postgres_store.connection() as database:
            assert database.raw.execute(sql.SQL('SELECT count(*) FROM {}').format(table)).fetchone()[0] == 0
            assert database.raw.execute('SHOW jit').fetchone()[0] == foreground_jit
        with postgres_store.connection(background=True) as database:
            assert database.raw.execute('SHOW jit').fetchone()[0] == 'off'
            assert database.raw.execute('SHOW transaction_timeout').fetchone()[0] == '250ms'
            assert database.raw.execute('SHOW lock_timeout').fetchone()[0] == '25ms'
            pid = database.raw.execute('SELECT pg_backend_pid()').fetchone()[0]
            database.raw.execute(sql.SQL('INSERT INTO {} VALUES (2)').format(table))
            started = time.perf_counter()
            assert [tuple(row) for row in database.raw.execute(query).fetchall()] == expected
            query_seconds = time.perf_counter() - started
        with pool.connection(timeout=.1) as raw:
            assert raw.execute('SELECT pg_backend_pid()').fetchone()[0] == pid
            assert tuple(raw.execute("SELECT current_setting('jit'),current_setting('transaction_timeout'),current_setting('lock_timeout')").fetchone()) == original
        with postgres_store.connection(read_only=True, repertoire_snapshot=True) as database:
            assert database.raw.execute('SHOW jit').fetchone()[0] == reader_jit
        with postgres_store.connection() as database:
            assert [row[0] for row in database.raw.execute(sql.SQL('SELECT marker FROM {}').format(table)).fetchall()] == [2]
        return dict(query_seconds=query_seconds, planner_cost=plan['Plan']['Total Cost'],
                    transaction_timeout='250ms', lock_timeout='25ms', rows=branches,
                    compiler_counterfactual_timed_out=True, rollback_and_session_reset=True)
    finally:
        try:
            with postgres_store.connection() as database:
                database.raw.execute(sql.SQL('DROP TABLE IF EXISTS {}').format(table))
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def main() -> None:
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("This check requires the disposable PostgreSQL test instance")
    os.environ["TEMPO_DATABASE_WRITE_URL"] = "postgresql://postgres@postgres:5432/tempo"
    os.environ.setdefault("TEMPO_DATABASE_READ_URL", os.environ["TEMPO_DATABASE_WRITE_URL"])
    os.environ["TEMPO_POSTGRES_POOL_SIZE"] = "1"
    report = check_background_jit_admission()
    print("PASS bounded background JIT admission", report)
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
