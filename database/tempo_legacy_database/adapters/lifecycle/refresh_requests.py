"""Essential daily queue readiness projection in the caller transaction."""


def daily_queue_projection(database, queue_date):
    return database.execute(
        "SELECT state,refresh_pending FROM queue_projections WHERE queue_date=?",
        (queue_date,),
    ).fetchone()
