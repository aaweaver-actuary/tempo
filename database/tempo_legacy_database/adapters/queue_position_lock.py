"""Serialize queue-position allocation across foreground and background workers."""

from __future__ import annotations

from tempo_legacy_database.adapters.postgres_store import PostgresConnection


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


def try_lock_discovery_queue_position(database: PostgresConnection, queue_date: str, card_id: str) -> bool:
    """Bound an optional admission while protecting any already-issued target."""
    prefix = database.execute_native(
        "SELECT pg_try_advisory_xact_lock_shared(hashtextextended('tempo:prefix-transition:reservations',0))",
    ).fetchone()
    if not prefix or not prefix[0]:
        return False
    day = database.execute_native(
        "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))",
        (f'tempo:daily-queue-position:{queue_date}',),
    ).fetchone()
    if not day or not day[0]:
        return False
    # Remaining borrowed writes fail and roll back rather than occupying the
    # foreground indefinitely if a noncooperating writer holds a row.
    database.execute_native("SELECT set_config('lock_timeout','100ms',true)")
    expected = database.execute_native(
        "SELECT COUNT(*) FROM daily_queue WHERE queue_date=%s AND card_id=%s AND status='queued'",
        (queue_date, card_id),
    ).fetchone()[0]
    targets = database.execute_native(
        "SELECT id FROM daily_queue WHERE queue_date=%s AND card_id=%s AND status='queued' FOR UPDATE SKIP LOCKED",
        (queue_date, card_id),
    ).fetchall()
    if len(targets) != expected:
        return False
    return not bool(database.execute_native(
        "SELECT 1 FROM queue_attempt_origins WHERE issued_as_head=1 AND queue_entry_id=ANY(%s::bigint[]) LIMIT 1",
        ([row[0] for row in targets],),
    ).fetchone())
