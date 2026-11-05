"""Existing queue maintenance statements on the caller-owned connection/transaction."""


def queue_projection_state(database, queue_date: str):
    return database.execute(
        "SELECT state FROM queue_projections WHERE queue_date=?",
        (queue_date,),
    ).fetchone()


def mark_queue_projection_failed(database, queue_date: str, sanitized_error: str):
    database.execute(
        """INSERT INTO queue_projections(
                       queue_date,state,generation,refresh_pending,last_error
                   ) VALUES(?,'failed',0,0,?)
                   ON CONFLICT(queue_date) DO UPDATE SET state='failed',
                       refresh_pending=0,last_error=excluded.last_error""",
        (queue_date, sanitized_error),
    )


def mark_queue_projection_refreshing(database, queue_date: str):
    database.execute(
        """INSERT INTO queue_projections(queue_date,state,generation,refresh_pending)
               VALUES(?,'refreshing',0,1)
               ON CONFLICT(queue_date) DO UPDATE SET state='refreshing',
                   refresh_pending=1,last_error=NULL""",
        (queue_date,),
    )
