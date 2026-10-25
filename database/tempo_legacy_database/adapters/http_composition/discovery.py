"""Existing discovery page/admission projections on the borrowed connection."""






def admission_intent(database, intent_id):
    return database.execute(
        "SELECT state,last_error FROM discovery_admission_intents WHERE id=?",
        (intent_id,),
    ).fetchone()


def admission_task(database, intent_id):
    return database.execute(
        """SELECT state,last_error FROM background_tasks
               WHERE kind='discovery_admission' AND deduplication_key=?""",
        (intent_id,),
    ).fetchone()
