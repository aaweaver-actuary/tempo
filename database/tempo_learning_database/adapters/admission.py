"""Existing Learning admission facts and writes; caller owns locks/transaction."""


def lock_card(database, card_id):
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("tempo:card-edit:" + card_id,),
    )
    return database.execute(
        "SELECT * FROM cards WHERE id=? FOR UPDATE", (card_id,)
    ).fetchone()


def protected_queue_ids(database, queue_day):
    return {
        row[0]
        for row in database.execute(
            """SELECT q.id FROM daily_queue q JOIN cards card ON card.id=q.card_id
           WHERE q.queue_date=? AND q.status='queued' AND
             (q.admission_kind='explicit' OR q.attempt_failed<>0
              OR q.attempt_state NOT IN ('clean','unstarted','gameplay','reinforcement')
              OR q.review_result_json IS NOT NULL
              OR EXISTS(SELECT 1 FROM queue_attempt_origins origin
                  WHERE origin.queue_entry_id=q.id AND origin.card_id=q.card_id
                    AND origin.revision=card.revision AND origin.issued_as_head=1)
              OR EXISTS(SELECT 1 FROM opening_evidence_attempts attempt
                  WHERE attempt.queue_entry_id=q.id))""",
            (queue_day,),
        ).fetchall()
    }


def card(database, card_id):
    return database.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()


def deleted_card(database, card_id):
    return database.execute(
        "SELECT 1 FROM deleted_cards WHERE card_id=?", (card_id,)
    ).fetchone()


def defense_enabled(database):
    return database.execute(
        "SELECT include_defensive_cards_in_daily_stack FROM settings WHERE id=1"
    ).fetchone()[0]


def eligible_study_exercise(database, exercise_id, queue_day):
    return database.execute(
        "SELECT 1 FROM study_exercises e JOIN studies s ON s.id=e.study_id WHERE e.id=? AND e.status='published' AND s.archived=0 AND NOT EXISTS(SELECT 1 FROM study_sibling_burials b WHERE b.exercise_id=e.id AND b.study_day=?)",
        (exercise_id, queue_day),
    ).fetchone()


def opening_route(database, card_id, allow_unexposed_parent, route_id):
    return database.execute(
        """SELECT step.repertoire_id FROM opening_graph_steps step JOIN opening_graph_publications publication
         ON publication.repertoire_id=step.repertoire_id AND publication.generation=step.generation
         JOIN repertoires repertoire ON repertoire.id=step.repertoire_id JOIN repertoire_lines line ON line.id=step.line_id
         WHERE step.card_id=? AND publication.state='ready' AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks b WHERE b.card_id=step.card_id AND b.repertoire_id=step.repertoire_id)
         AND (?=1 OR step.parent_card_id IS NULL OR EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=step.parent_card_id AND r.source_kind='study' AND r.invalidated_at IS NULL))
         AND (?='' OR step.repertoire_id=?)
         ORDER BY repertoire.created_at,repertoire.id,line.created_at,line.id,step.decision_index LIMIT 1""",
        (card_id, allow_unexposed_parent, route_id, route_id),
    ).fetchone()


def buried_entry(database, card_id, queue_day):
    return database.execute(
        "SELECT 1 FROM daily_queue WHERE card_id=? AND queue_date=? AND status='buried'",
        (card_id, queue_day),
    ).fetchone()


def queued_entry_ids(database, card_id, queue_day):
    return database.execute(
        "SELECT id FROM daily_queue WHERE card_id=? AND queue_date=? AND status='queued'",
        (card_id, queue_day),
    ).fetchall()


def retain_admission_membership(database, membership, card_id, queue_day):
    database.execute(
        "UPDATE daily_queue SET admission_repertoire_id=COALESCE(admission_repertoire_id,?) WHERE card_id=? AND queue_date=? AND status='queued'",
        (membership, card_id, queue_day),
    )


def introduce_missed_card(database, queue_day, card_id):
    database.execute(
        "UPDATE cards SET introduced_at=COALESCE(introduced_at,?),state=CASE WHEN state IN ('new','locked') THEN 'learning' ELSE state END WHERE id=?",
        (queue_day, card_id),
    )


def existing_entry(database, card_id, queue_day):
    return database.execute(
        "SELECT 1 FROM daily_queue WHERE card_id=? AND queue_date=?",
        (card_id, queue_day),
    ).fetchone()


def admission_settings(database):
    return database.execute("SELECT * FROM settings WHERE id=1").fetchone()


def opening_limit(database, default_limit, membership):
    return database.execute(
        "SELECT COALESCE(new_cards_per_day,?) FROM repertoires WHERE id=?",
        (default_limit, membership),
    ).fetchone()[0]


def used_opening_quota(database, queue_day, membership):
    return database.execute(
        "SELECT COUNT(DISTINCT card_id) FROM queue_attempt_origins WHERE queue_date=? AND cycle=0 AND admission_kind='new' AND content_type='opening' AND admission_repertoire_id=?",
        (queue_day, membership),
    ).fetchone()[0]


def used_family_quota(database, queue_day, content_type):
    return database.execute(
        "SELECT COUNT(DISTINCT card_id) FROM queue_attempt_origins WHERE queue_date=? AND cycle=0 AND admission_kind='new' AND content_type=?",
        (queue_day, content_type),
    ).fetchone()[0]


def next_position(database, queue_day):
    return database.execute(
        "SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?",
        (queue_day,),
    ).fetchone()[0]


def insert_entry(database, queue_day, card_id, position, content_type, admission_kind, membership, reason):
    database.execute(
        "INSERT INTO daily_queue(queue_date,card_id,position,card_bucket,admission_kind,admission_repertoire_id,gameplay_priority_reason) VALUES(?,?,?,?,?,?,?)",
        (queue_day, card_id, position, content_type, admission_kind, membership, reason),
    )


def introduce_card(database, queue_day, card_id):
    database.execute(
        "UPDATE cards SET introduced_at=COALESCE(introduced_at,?),state=CASE WHEN state='new' THEN 'learning' ELSE state END WHERE id=?",
        (queue_day, card_id),
    )


def next_candidates(database, day, cursor):
    # The #235 route tuple is retained, with frozen preference ahead of it.
    return [
        dict(row)
        for row in database.execute_native(
            """WITH candidates AS (
      SELECT c.id,ARRAY[
        CASE WHEN c.introduced_at IS NOT NULL THEN '0' WHEN c.content_type='opening' THEN '1' ELSE '2' END,
        LPAD(COALESCE(rank.ordinal,9223372036854775807)::text,20,'0'),
        COALESCE(repertoire.created_at,''),COALESCE(repertoire.id,''),
        COALESCE(line.created_at,''),COALESCE(line.id,''),
        LPAD(COALESCE(step.decision_index,0)::text,20,'0'),c.id] AS sort_key
      FROM cards c LEFT JOIN opening_graph_steps step ON step.card_id=c.id AND step.generation=c.publication_generation
      LEFT JOIN repertoires repertoire ON repertoire.id=step.repertoire_id
      LEFT JOIN repertoire_lines line ON line.id=step.line_id
      LEFT JOIN frozen_opening_activation active ON active.id=1
      LEFT JOIN frozen_opening_ranks rank ON rank.run_id=active.run_id AND rank.card_id=c.id
      WHERE c.serving_active=1 AND c.archived=0 AND c.due_date<=%s)
      SELECT id,sort_key FROM candidates WHERE sort_key>%s::text[] ORDER BY sort_key LIMIT 16""",
            (day, cursor),
        ).fetchall()
    ]
