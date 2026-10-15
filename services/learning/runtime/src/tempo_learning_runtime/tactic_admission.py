def lock_daily_tactic_admission(database, day):
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("tempo:tactic-admission:" + day,),
    )
