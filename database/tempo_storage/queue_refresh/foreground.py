"""Existing foreground queue refresh projection on the caller transaction."""
_QUEUE_REFRESH_PROJECTION_SQL = "INSERT INTO queue_projections(queue_date,state,generation,refresh_pending)\n           VALUES(%s,'refreshing',0,1) ON CONFLICT(queue_date) DO UPDATE SET\n           state='refreshing',refresh_pending=1,last_error=NULL"

def mark_queue_refreshing(database, queue_date):
    database.execute(_QUEUE_REFRESH_PROJECTION_SQL, (queue_date,))

def signal_queue_refresh_in_transaction(database, queue_date):
    """Record one committed request after the singleton task has been locked.

    The caller owns the transaction and must lock the task before this row.
    Existing error details remain available until an actual successful slice.
    """
    row = database.execute("INSERT INTO queue_projections(\n               queue_date,state,generation,refresh_pending,requested_refresh_epoch,\n               published_refresh_epoch)\n           VALUES(%s,'refreshing',0,1,1,0)\n           ON CONFLICT(queue_date) DO UPDATE SET\n               requested_refresh_epoch=queue_projections.requested_refresh_epoch+1,\n               state='refreshing',refresh_pending=1\n           RETURNING requested_refresh_epoch", (queue_date,)).fetchone()
    return int(row[0])

def lock_refresh_epochs(database, queue_date):
    """Read and lock the projection after the caller has locked the task."""
    statement = 'SELECT requested_refresh_epoch,published_refresh_epoch FROM queue_projections WHERE queue_date=%s'
    statement += ' FOR UPDATE'
    return database.execute(statement, (queue_date,)).fetchone()

def publish_refresh_epoch_if_current(database, queue_date, cycle_epoch):
    """Fence covered work; caller publishes readiness and task completion atomically.

    Caller holds task then projection locks and must roll back if later publication
    or completion fails. This operation never changes readiness by itself.
    """
    return database.execute('UPDATE queue_projections SET published_refresh_epoch=%s\n           WHERE queue_date=%s AND requested_refresh_epoch=%s\n             AND published_refresh_epoch<=%s', (cycle_epoch, queue_date, cycle_epoch, cycle_epoch)).rowcount == 1
