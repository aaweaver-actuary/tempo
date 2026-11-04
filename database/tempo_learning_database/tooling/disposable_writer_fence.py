"""Unselected, disposable-only PostgreSQL admission/session fence prototype.

No credential discovery, launcher integration, source export or reopen capability.
The allocating runner must supply catalog tokens and independently bound identity.
"""

from dataclasses import dataclass
import re
import time

from psycopg import sql


@dataclass(frozen=True)
class DisposableSource:
    database: str
    database_oid: int
    cluster_system_identifier: str
    allocation_token: str


@dataclass(frozen=True)
class FenceReceipt:
    database: str
    database_oid: int
    cluster_system_identifier: str
    remaining_sessions: int = 0
    status: str = "disposable_admission_session_denied"


class FenceHeld(RuntimeError):
    """No successful fence receipt; admission may already be closed. Never reopen."""


def fence_disposable_source(control, source: DisposableSource, *, timeout_seconds=5.0):
    """Close admission and drain every backend of one verified disposable DB.

    Caller owns this exclusive administrative session, its bounded SQL settings,
    and the allocating runner's cluster. Catalog markers are allocation evidence,
    not protection against a malicious administrator capable of forging them.
    """
    if (not control.autocommit or not isinstance(source, DisposableSource)
            or not re.fullmatch(r"[0-9a-f]{32}", source.allocation_token)
            or not source.database.startswith("learning_")
            or not 0 < timeout_seconds <= 30):
        raise FenceHeld("allocation_or_control_invalid")
    marker = "tempo-disposable-fence:" + source.allocation_token
    row = control.execute("""
        SELECT current_database(), system_identifier::text,
               shobj_description((SELECT oid FROM pg_database WHERE datname='postgres'), 'pg_database'),
               (SELECT rolsuper FROM pg_roles WHERE rolname=current_user),
               (SELECT setting::int BETWEEN 1 AND 1000 FROM pg_settings WHERE name='statement_timeout'),
               (SELECT setting::int BETWEEN 1 AND 1000 FROM pg_settings WHERE name='lock_timeout')
        FROM pg_control_system()
    """).fetchone()
    if (row[0] == source.database or row[1] != source.cluster_system_identifier
            or row[2] != marker or not all(row[3:])):
        raise FenceHeld("cluster_allocation_or_control_mismatch")

    def verify_source():
        actual = control.execute("""
            SELECT oid, shobj_description(oid, 'pg_database'), datallowconn
            FROM pg_database WHERE datname=%s
        """, (source.database,)).fetchone()
        if not actual or actual[:2] != (source.database_oid, marker):
            raise FenceHeld("source_allocation_mismatch")
        if control.execute(
                "SELECT count(*) FROM pg_prepared_xacts WHERE database=%s",
                (source.database,)).fetchone()[0]:
            raise FenceHeld("prepared_transactions")
        return actual[2]

    verify_source()
    # Administrative DDL commits before backend termination. A failed drain leaves
    # admission closed; no successful return is possible while a backend survives.
    control.execute(sql.SQL("ALTER DATABASE {} ALLOW_CONNECTIONS false").format(
        sql.Identifier(source.database)))
    deadline = time.monotonic() + timeout_seconds
    while True:
        if verify_source():
            raise FenceHeld("admission_reopened")
        pids = control.execute(
            "SELECT pid FROM pg_stat_activity WHERE datid=%s",
            (source.database_oid,)).fetchall()
        if not pids:
            return FenceReceipt(source.database, source.database_oid,
                                source.cluster_system_identifier)
        if time.monotonic() >= deadline:
            raise FenceHeld("drain_incomplete")
        for (pid,) in pids:
            control.execute("SELECT pg_terminate_backend(%s)", (pid,))
        time.sleep(min(0.02, max(0, deadline - time.monotonic())))
