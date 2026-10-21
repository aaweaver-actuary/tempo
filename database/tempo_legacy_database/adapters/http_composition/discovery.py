"""Existing discovery page/admission projections on the borrowed connection."""


def discovery_counts(database, now):
    return database.execute(
        """SELECT COUNT(*), SUM(CASE WHEN opportunity.seen_at IS NULL
                       AND opportunity.snoozed_until IS NULL
                       OR opportunity.snoozed_until<=? THEN 1 ELSE 0 END)
               FROM current_repertoire_opportunities opportunity
               WHERE opportunity.status='active'
                 AND opportunity.canonical_prefix_revision=(SELECT canonical_prefix_revision FROM repertoires WHERE id=opportunity.repertoire_id)
                 AND opportunity.handled_evidence_json IS NULL
                 AND opportunity.repertoire_id NOT IN
                     ('__tactics__','__endgames__','__game_mistakes__','__game_tactics__','__captured_tactics__','__defense__','__retained_cards__')""",
        (now,),
    ).fetchone()


def discovery_identifiers(database, limit, offset):
    yield from database.execute(
        """SELECT id,repertoire_id FROM current_repertoire_opportunities
               WHERE status='active'
                 AND handled_evidence_json IS NULL
                 AND repertoire_id NOT IN
                   ('__tactics__','__endgames__','__game_mistakes__','__game_tactics__','__captured_tactics__','__defense__','__retained_cards__')
               ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?""",
        (limit, offset),
    )


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
