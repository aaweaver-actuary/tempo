"""Queue freshness facts on the caller's transaction; task lock comes first."""


def request(database, day):
    row = database.execute(
        """INSERT INTO queue_projections(queue_date,state,generation,refresh_pending,
                    requested_refresh_epoch,published_refresh_epoch)
           VALUES(?,'refreshing',0,1,1,0)
           ON CONFLICT(queue_date) DO UPDATE SET
               requested_refresh_epoch=queue_projections.requested_refresh_epoch+1,
               state='refreshing',refresh_pending=1
           RETURNING requested_refresh_epoch""", (day,),
    ).fetchone()
    return int(row[0])


def lock(database, day):
    return database.execute(
        "SELECT requested_refresh_epoch,published_refresh_epoch FROM queue_projections "
        "WHERE queue_date=? FOR UPDATE", (day,),
    ).fetchone()


def publish_if_current(database, day, epoch, updated_at):
    """Publish only the pinned cycle; caller must atomically complete its lease."""
    return database.execute(
        """UPDATE queue_projections SET published_refresh_epoch=?,state='ready',
               generation=generation+1,refresh_pending=0,last_error=NULL,updated_at=?
           WHERE queue_date=? AND requested_refresh_epoch=? AND published_refresh_epoch<=?""",
        (epoch, updated_at, day, epoch, epoch),
    ).rowcount == 1
