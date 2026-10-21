"""Source-neutral tactic introduction accounting, shared by both stores."""

DAILY_TACTIC_COUNT_SQL = """SELECT COUNT(*) FROM cards
    WHERE content_type='tactic' AND introduced_at=? AND archived=0
      AND superseded_by IS NULL"""


def count_daily_tactic_introductions(database, queue_date: str) -> int:
    return int(database.execute(DAILY_TACTIC_COUNT_SQL, (queue_date,)).fetchone()[0])


def lock_daily_tactic_admission(database, queue_date: str) -> None:
    # SQLite's foreground writer already serializes the complete transaction.
    if hasattr(database, "execute_native"):
        database.execute_native(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"tempo:tactic-daily-admission:{queue_date}",),
        )
