"""Daily-statistics job admission row, borrowing the command transaction."""


def queue_daily_statistics(database, local_day: str, updated_at: str) -> None:
    database.execute(
        """INSERT INTO daily_statistics_jobs(local_day,status,last_error,updated_at)
           VALUES(?,'queued',NULL,?) ON CONFLICT(local_day) DO UPDATE SET
           status='queued',last_error=NULL,updated_at=excluded.updated_at""",
        (local_day, updated_at),
    )
