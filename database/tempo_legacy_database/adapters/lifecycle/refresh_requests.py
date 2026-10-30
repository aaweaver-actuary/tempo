"""Essential daily queue readiness projection in the caller transaction."""


def daily_queue_projection(database, queue_date):
    return database.execute(
        """SELECT projection.state,projection.refresh_pending,
                  (SELECT day.membership_hash FROM daily_queue_days AS day
                   WHERE day.queue_date=projection.queue_date) AS presentation_membership_hash,
                  EXISTS(SELECT 1 FROM daily_queue AS entry
                         WHERE entry.queue_date=projection.queue_date AND entry.status='queued') AS has_queued_entries
           FROM queue_projections AS projection WHERE projection.queue_date=?""",
        (queue_date,),
    ).fetchone()
