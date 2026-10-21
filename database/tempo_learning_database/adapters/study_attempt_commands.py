"""Study command reads and locks on the caller-owned transaction."""


def review_card_for_update(database, card_id, exercise_id):
    return database.execute(
        """SELECT * FROM cards WHERE id=? AND study_exercise_id=?
           AND archived=0 AND pending_validation=0 AND serving_active=1 FOR UPDATE""",
        (card_id, exercise_id),
    ).fetchone()


def review_queue_date(database, queue_entry_id):
    return database.execute(
        "SELECT queue_date FROM daily_queue WHERE id=?",
        (queue_entry_id,),
    ).fetchone()


def review_queue_for_update(database, queue_entry_id, card_id, cycle):
    return database.execute(
        """SELECT * FROM daily_queue WHERE id=? AND card_id=? AND cycle=?
           AND status='queued' FOR UPDATE""",
        (queue_entry_id, card_id, cycle),
    ).fetchone()


def existing_queue_answer(database, queue_entry_id):
    return database.execute(
        "SELECT 1 FROM study_attempts WHERE queue_entry_id=?",
        (queue_entry_id,),
    ).fetchone()


def latest_review_id(database, card_id):
    return database.execute(
        "SELECT COALESCE(MAX(id),0) FROM reviews WHERE card_id=?",
        (card_id,),
    ).fetchone()[0]


def lock_attempt_identity(database, attempt_id):
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"tempo:study-attempt:{attempt_id}",),
    )


def attempt_by_id(database, attempt_id):
    return database.execute(
        "SELECT * FROM study_attempts WHERE id=?",
        (attempt_id,),
    ).fetchone()


def exercise_owner(database, exercise_id):
    return database.execute(
        "SELECT study_id FROM study_exercises WHERE id=?",
        (exercise_id,),
    ).fetchone()


def exercise_for_update(database, exercise_id):
    return database.execute(
        "SELECT * FROM study_exercises WHERE id=? FOR UPDATE",
        (exercise_id,),
    ).fetchone()


def serving_exercise_card(database, exercise_id):
    return database.execute(
        "SELECT 1 FROM cards WHERE study_exercise_id=? AND serving_active=1 AND archived=0",
        (exercise_id,),
    ).fetchone()


def exercise_position(database, position_id):
    return database.execute(
        "SELECT fen FROM study_positions WHERE id=?",
        (position_id,),
    ).fetchone()


def revision_specification(database, exercise_id, revision):
    return database.execute(
        "SELECT specification_json FROM study_exercise_revisions WHERE exercise_id=? AND revision=?",
        (exercise_id, revision),
    ).fetchone()


def record_attempt(database, *, attempt_id, exercise_id, revision, card_id, queue_entry_id,
                   cycle, context, answer_json, answer_hash, assessment_json,
                   assessment_method, grader_version, hint_seen,
                   solution_seen_before_answer, started_at, committed_at):
    database.execute(
        """INSERT INTO study_attempts(id,exercise_id,revision,card_id,queue_entry_id,cycle,context,
           answer_json,answer_hash,assessment_json,assessment_method,grader_version,hint_seen,
           solution_seen_before_answer,started_at,committed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            attempt_id, exercise_id, revision, card_id, queue_entry_id, cycle, context,
            answer_json, answer_hash, assessment_json, assessment_method, grader_version,
            hint_seen, solution_seen_before_answer, started_at, committed_at,
        ),
    )


def pending_assessment_attempt(database, attempt_id):
    return database.execute(
        "SELECT card_id,queue_entry_id,context FROM study_attempts WHERE id=?",
        (attempt_id,),
    ).fetchone()


def lock_assessment_card(database, card_id):
    database.execute(
        "SELECT id FROM cards WHERE id=? FOR UPDATE",
        (card_id,),
    )


def lock_assessment_queue(database, queue_entry_id):
    database.execute(
        "SELECT id FROM daily_queue WHERE id=? FOR UPDATE",
        (queue_entry_id,),
    )


def assessment_attempt_for_update(database, attempt_id, exercise_id):
    return database.execute(
        "SELECT * FROM study_attempts WHERE id=? AND exercise_id=? FOR UPDATE",
        (attempt_id, exercise_id),
    ).fetchone()


def assessment_exercise(database, exercise_id):
    return database.execute(
        "SELECT * FROM study_exercises WHERE id=?",
        (exercise_id,),
    ).fetchone()
