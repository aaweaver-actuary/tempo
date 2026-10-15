def lock_queue_date_for_position(database, queue_date):
    database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("tempo:queue-position:"+queue_date,))
