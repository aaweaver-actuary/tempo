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
