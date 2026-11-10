"""Tactic introduction accounting inside the caller's PostgreSQL transaction."""

DAILY_TACTIC_COUNT_SQL = "SELECT COUNT(*) FROM cards\n    WHERE content_type='tactic' AND introduced_at=%s AND archived=0\n      AND superseded_by IS NULL"


def count_daily_tactic_introductions(database, queue_date: str) -> int:
    return int(database.execute(DAILY_TACTIC_COUNT_SQL, (queue_date,)).fetchone()[0])


def lock_daily_tactic_admission(database, queue_date: str) -> None:
    database.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"tempo:tactic-daily-admission:{queue_date}",),
    )
