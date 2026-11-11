"""Essential daily queue readiness projection in the caller transaction."""


def daily_queue_projection(database, queue_date):
    return database.execute(
        "SELECT projection.state,projection.refresh_pending,\n                  (SELECT day.membership_hash FROM daily_queue_days AS day\n                   WHERE day.queue_date=projection.queue_date) AS presentation_membership_hash,\n                  EXISTS(SELECT 1 FROM daily_queue AS entry\n                         WHERE entry.queue_date=projection.queue_date AND entry.status='queued') AS has_queued_entries\n           FROM queue_projections AS projection WHERE projection.queue_date=%s",
        (queue_date,),
    ).fetchone()
