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


def existing_defense_admission(database, today: str):
    return database.execute(
        """SELECT 1 FROM background_tasks WHERE kind='defensive_admission'
               AND deduplication_key=? LIMIT 1""",
        (today,),
    ).fetchone()


def eligible_defense_admission(database):
    return database.execute(
        """SELECT 1 FROM threat_training_candidates
               WHERE validation_state IN ('engine_supported','validated_control')
               AND approved_at IS NULL AND dismissed_at IS NULL AND paused_at IS NULL
               AND superseded_at IS NULL
               LIMIT 1""",
    ).fetchone()


def pending_discovery_admission(database):
    return database.execute(
        """SELECT admission.id FROM discovery_admission_intents admission
               WHERE admission.state!='queued'
                 AND NOT EXISTS(SELECT 1 FROM background_tasks task
                     WHERE task.kind='discovery_admission' AND task.deduplication_key=admission.id)
               ORDER BY admission.created_at,admission.id LIMIT 1""",
    ).fetchone()


def mark_queue_projection_refreshing(database, queue_date: str):
    database.execute(
        """INSERT INTO queue_projections(queue_date,state,generation,refresh_pending)
               VALUES(?,'refreshing',0,1)
               ON CONFLICT(queue_date) DO UPDATE SET state='refreshing',
                   refresh_pending=1,last_error=NULL""",
        (queue_date,),
    )
