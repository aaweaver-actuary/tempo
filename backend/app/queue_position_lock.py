"""Serialize queue-position allocation across foreground and background workers."""

from __future__ import annotations

from .postgres_store import PostgresConnection


def lock_queue_date_for_position(database: PostgresConnection, queue_date: str) -> None:
    # Join the transition boundary before reading/allocating positions. Ordinary
    # producers retain their date lock; transitions reserve all dates once.
    database.execute_native(
        "SELECT pg_advisory_xact_lock_shared(hashtextextended('tempo:prefix-transition:reservations',0))", (),
    )
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"tempo:daily-queue-position:{queue_date}",),
    )
