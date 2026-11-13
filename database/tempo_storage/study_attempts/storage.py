"""Fixed exercise-attempt, review-queue and sibling-burial operations."""


def active_review_card(database, card_id, exercise_id):
    return database.execute(
            'SELECT * FROM cards WHERE id=%s AND study_exercise_id=%s AND archived=0 AND pending_validation=0',
            (card_id, exercise_id),
        ).fetchone()


def queued_review_entry(database, queue_entry_id, card_id, cycle):
    return database.execute(
            "SELECT * FROM daily_queue WHERE id=%s AND card_id=%s AND cycle=%s AND status='queued'",
            (queue_entry_id, card_id, cycle),
        ).fetchone()


def light_interval_setting(database):
    return database.execute(
            "SELECT light_first_interval_days FROM settings WHERE id=1",
        ).fetchone()


def complete_queue_entry(database, attempt_state, queue_entry_id):
    database.execute(
            "UPDATE daily_queue SET status='complete',attempt_state=%s WHERE id=%s",
            (attempt_state, queue_entry_id),
        )


def latest_review_id(database, card_id):
    return database.execute(
                'SELECT MAX(id) FROM reviews WHERE card_id=%s', (card_id,),
            ).fetchone()[0]


def finalize_attempt(database, now, result_json, attempt_id):
    database.execute(
        'UPDATE study_attempts SET finalized_at=%s,result_json=%s WHERE id=%s',
        (now, result_json, attempt_id),
    )


def retain_queue_result(database, result_json, queue_entry_id):
    database.execute(
            'UPDATE daily_queue SET review_result_json=%s WHERE id=%s',
            (result_json, queue_entry_id),
        )


def exercise(database, exercise_id):
    return database.execute(
            'SELECT * FROM study_exercises WHERE id=%s', (exercise_id,),
        ).fetchone()


def sibling_exercises(database, study_id, exercise_id, position_id, sibling_group):
    rows = database.execute(
            'SELECT id FROM study_exercises WHERE study_id=%s AND id!=%s\n               AND (position_id=%s OR (sibling_group IS NOT NULL AND sibling_group=%s))',
            (study_id, exercise_id, position_id, sibling_group),
        )
    return (row for row in rows)


def bury_sibling(database, sibling_id, day, reason):
    database.execute(
                'INSERT INTO study_sibling_burials VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
                (sibling_id, day, reason),
            )


def block_sibling_queue(database, day, sibling_id):
    database.execute(
                "UPDATE daily_queue SET status='blocked' WHERE queue_date=%s\n                   AND status='queued' AND card_id IN\n                   (SELECT id FROM cards WHERE study_exercise_id=%s)",
                (day, sibling_id),
            )


def locked_review_card(database, card_id, exercise_id):
    return database.execute(
        'SELECT * FROM cards WHERE id=%s AND study_exercise_id=%s\n           AND archived=0 AND pending_validation=0 FOR UPDATE',
        (card_id, exercise_id),
    ).fetchone()


def queue_day(database, queue_entry_id):
    return database.execute(
        'SELECT queue_date FROM daily_queue WHERE id=%s', (queue_entry_id,),
    ).fetchone()


def locked_review_entry(database, queue_entry_id, card_id, cycle):
    return database.execute(
        "SELECT * FROM daily_queue WHERE id=%s AND card_id=%s AND cycle=%s\n           AND status='queued' FOR UPDATE",
        (queue_entry_id, card_id, cycle),
    ).fetchone()


def answered_queue_entry(database, queue_entry_id):
    return database.execute(
        'SELECT 1 FROM study_attempts WHERE queue_entry_id=%s', (queue_entry_id,),
    ).fetchone()


def latest_effective_review_id(database, card_id):
    return database.execute(
            'SELECT COALESCE(MAX(id),0) FROM reviews WHERE card_id=%s', (card_id,),
        ).fetchone()[0]


def lock_attempt_identity(database, attempt_id):
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"tempo:study-attempt:{attempt_id}",),
    )


def attempt(database, attempt_id):
    return database.execute(
        'SELECT * FROM study_attempts WHERE id=%s', (attempt_id,),
    ).fetchone()


def exercise_owner(database, exercise_id):
    return database.execute(
            'SELECT study_id FROM study_exercises WHERE id=%s', (exercise_id,),
        ).fetchone()


def locked_exercise(database, exercise_id):
    return database.execute(
        'SELECT * FROM study_exercises WHERE id=%s FOR UPDATE', (exercise_id,),
    ).fetchone()


def exercise_position(database, position_id):
    return database.execute(
        'SELECT fen FROM study_positions WHERE id=%s', (position_id,),
    ).fetchone()


def exercise_revision(database, exercise_id, revision):
    return database.execute(
        'SELECT specification_json FROM study_exercise_revisions WHERE exercise_id=%s AND revision=%s',
        (exercise_id, revision),
    ).fetchone()


def insert_attempt(database, values):
    database.execute(
        'INSERT INTO study_attempts(id,exercise_id,revision,card_id,queue_entry_id,cycle,context,\n           answer_json,answer_hash,assessment_json,assessment_method,grader_version,hint_seen,\n           solution_seen_before_answer,started_at,committed_at)\n           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        values,
    )


def pending_attempt(database, attempt_id):
    return database.execute(
        'SELECT card_id,queue_entry_id,context FROM study_attempts WHERE id=%s',
        (attempt_id,),
    ).fetchone()


def lock_attempt_card(database, card_id):
    database.execute(
            'SELECT id FROM cards WHERE id=%s FOR UPDATE', (card_id,),
        )


def lock_attempt_queue_entry(database, queue_entry_id):
    database.execute(
            'SELECT id FROM daily_queue WHERE id=%s FOR UPDATE',
            (queue_entry_id,),
        )


def locked_attempt(database, attempt_id, exercise_id):
    return database.execute(
        'SELECT * FROM study_attempts WHERE id=%s AND exercise_id=%s FOR UPDATE',
        (attempt_id, exercise_id),
    ).fetchone()


def frozen_presentation(database, study_id, exercise_id):
    """Read only a published exercise and its study-owned source position."""
    return database.execute(
        """SELECT e.id,e.current_revision,r.specification_json,p.fen
           FROM study_exercises e JOIN studies s ON s.id=e.study_id
           JOIN study_positions p ON p.id=e.position_id
           JOIN study_sources src ON src.id=p.source_id
           JOIN study_chapters chapter ON chapter.id=src.chapter_id AND chapter.study_id=e.study_id
           JOIN study_exercise_revisions r ON r.exercise_id=e.id AND r.revision=e.current_revision
           WHERE e.id=%s AND e.study_id=%s AND e.status='published' AND s.archived=0""",
        (exercise_id, study_id),
    ).fetchone()


def frozen_attempt_feedback(database, study_id, exercise_id, attempt_id):
    """Bind feedback to the original attempt and its persisted revision."""
    return database.execute(
        """SELECT r.specification_json FROM study_attempts a
           JOIN study_exercises e ON e.id=a.exercise_id
           JOIN study_exercise_revisions r ON r.exercise_id=a.exercise_id AND r.revision=a.revision
           WHERE a.id=%s AND a.exercise_id=%s AND e.study_id=%s""",
        (attempt_id, exercise_id, study_id),
    ).fetchone()


def issued_frozen_presentation(database, study_id, exercise_id, card_id, entry_id, cycle, revision):
    """Read the exact current revision of a previously issued Study queue card."""
    return database.execute(
        """SELECT e.id,e.current_revision,r.specification_json,p.fen
           FROM study_exercises e JOIN cards c ON c.study_exercise_id=e.id
           JOIN daily_queue q ON q.card_id=c.id
           JOIN queue_attempt_origins origin ON origin.queue_entry_id=q.id AND origin.card_id=c.id
             AND origin.revision=c.revision AND origin.queue_date=q.queue_date AND origin.cycle=q.cycle
             AND origin.issued_as_head=1 AND origin.content_type='study_exercise'
             AND origin.start_fen=c.start_fen AND origin.moves_json=c.moves_json
           JOIN study_positions p ON p.id=e.position_id AND p.fen=origin.start_fen
           JOIN study_sources src ON src.id=p.source_id
           JOIN study_chapters chapter ON chapter.id=src.chapter_id AND chapter.study_id=e.study_id
           JOIN study_exercise_revisions r ON r.exercise_id=e.id AND r.revision=e.current_revision
           WHERE e.id=%s AND e.study_id=%s AND c.id=%s AND q.id=%s AND q.cycle=%s
             AND c.revision=%s AND e.current_revision=c.revision AND q.status='queued'
             AND c.archived=0 AND c.pending_validation=0 AND c.superseded_by IS NULL
             AND NOT EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id=c.id)""",
        (exercise_id, study_id, card_id, entry_id, cycle, revision),
    ).fetchone()


def pending_frozen_presentation(database, study_id, exercise_id, attempt_id):
    """Require original issued position evidence for pending review recovery.

    Practice attempts do not retain an immutable FEN, so they remain held when
    the ordinary presentation is unavailable.
    """
    return database.execute(
        """SELECT e.id,a.revision AS current_revision,r.specification_json,p.fen
           FROM study_attempts a JOIN study_exercises e ON e.id=a.exercise_id
           JOIN study_exercise_revisions r ON r.exercise_id=a.exercise_id AND r.revision=a.revision
           JOIN cards c ON c.id=a.card_id AND c.study_exercise_id=e.id AND c.revision=a.revision
           JOIN queue_attempt_origins origin ON origin.queue_entry_id=a.queue_entry_id AND origin.card_id=c.id
             AND origin.revision=a.revision AND origin.cycle=a.cycle AND origin.issued_as_head=1
             AND origin.content_type='study_exercise' AND origin.start_fen=c.start_fen AND origin.moves_json=c.moves_json
           JOIN study_positions p ON p.id=e.position_id AND p.fen=origin.start_fen
           JOIN study_sources src ON src.id=p.source_id
           JOIN study_chapters chapter ON chapter.id=src.chapter_id AND chapter.study_id=e.study_id
           WHERE a.id=%s AND e.id=%s AND e.study_id=%s AND a.context='review' AND a.result_json IS NULL
             AND a.retired_operation_id IS NULL AND a.assessment_method='self_assessed'
             AND e.current_revision=a.revision""",
        (attempt_id, exercise_id, study_id),
    ).fetchone()


def issued_review_source_state(database, exercise_id, card_id, entry_id, cycle, revision):
    """None if no issued origin; otherwise whether its source remains exact."""
    row = database.execute(
        """SELECT (origin.start_fen=c.start_fen AND origin.moves_json=c.moves_json
                    AND p.fen=origin.start_fen) AS source_current
           FROM queue_attempt_origins origin JOIN cards c ON c.id=origin.card_id
           JOIN daily_queue q ON q.id=origin.queue_entry_id AND q.card_id=c.id
           JOIN study_exercises e ON e.id=c.study_exercise_id
           JOIN study_positions p ON p.id=e.position_id
           WHERE origin.queue_entry_id=%s AND origin.card_id=%s AND origin.cycle=%s
             AND origin.revision=%s AND origin.queue_date=q.queue_date AND q.cycle=origin.cycle
             AND origin.issued_as_head=1 AND origin.content_type='study_exercise'
             AND e.id=%s AND e.current_revision=origin.revision AND c.revision=origin.revision""",
        (entry_id, card_id, cycle, revision, exercise_id),
    ).fetchone()
    return None if row is None else bool(row['source_current'])


def study_is_active(database, study_id):
    row = database.execute('SELECT archived FROM studies WHERE id=%s', (study_id,)).fetchone()
    return row is not None and row[0] == 0


def completed_review_requeue(database, study_id, exercise_id, attempt_id):
    """Read an exact finalized parent and its current queued repeat; never issue."""
    import json

    parent = database.execute(
        """SELECT a.id,a.card_id,a.revision,a.result_json
           FROM study_attempts a JOIN study_exercises e ON e.id=a.exercise_id
           WHERE a.id=%s AND a.exercise_id=%s AND e.study_id=%s
             AND a.context='review' AND a.finalized_at IS NOT NULL""",
        (attempt_id, exercise_id, study_id),
    ).fetchone()
    if not parent or not parent['result_json']:
        return None
    try:
        result = json.loads(parent['result_json'])
        review = result['review']
        entry_id, review_id = review['requeue_entry_id'], review['review_id']
        if (result['attempt_id'] != attempt_id or result['persisted'] is not True or
            type(entry_id) is not int or entry_id <= 0 or type(review_id) is not int or review_id <= 0):
            return None
    except (TypeError, ValueError, KeyError):
        return None
    entry = database.execute(
        """SELECT q.id,q.card_id,q.cycle,c.revision
           FROM daily_queue q JOIN cards c ON c.id=q.card_id
           WHERE q.id=%s AND q.card_id=%s AND q.status='queued'
             AND c.revision=%s AND c.study_exercise_id=%s""",
        (entry_id, parent['card_id'], parent['revision'], exercise_id),
    ).fetchone()
    if not entry:
        return None
    return {'parent_attempt_id': attempt_id, 'review_id': review_id,
            'queue_entry_id': entry['id'], 'card_id': entry['card_id'],
            'cycle': entry['cycle'], 'revision': entry['revision']}
