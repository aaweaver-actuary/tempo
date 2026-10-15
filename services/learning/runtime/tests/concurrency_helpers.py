"""Observe actual PostgreSQL contention before releasing test-owned gates."""

import time
import psycopg


def wait_for_lock(case, backend_pid):
    with psycopg.connect(case.admin, autocommit=True) as inspector:
        for _ in range(150):
            row = inspector.execute(
                "SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s",
                (backend_pid,),
            ).fetchone()
            if row and row[0] == "Lock":
                return
            time.sleep(0.005)
    raise AssertionError("Contender never waited on a PostgreSQL lock")


def extend_gate_budget(database):
    # Only controlled concurrency tests wait on human-scale event gates. The
    # ordinary bounded-contention tests retain the actual production budgets.
    database.execute_native("SET LOCAL transaction_timeout='8s'")
    database.execute_native("SET LOCAL statement_timeout='5s'")
    database.execute_native("SET LOCAL lock_timeout='4s'")
