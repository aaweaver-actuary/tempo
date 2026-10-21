"""Fixed exercise-attempt, review-queue and sibling-burial operations."""


def active_review_card(database, card_id, exercise_id):
    return database.execute(
            "SELECT * FROM cards WHERE id=? AND study_exercise_id=? AND archived=0 AND pending_validation=0",
            (card_id, exercise_id),
        ).fetchone()


def queued_review_entry(database, queue_entry_id, card_id, cycle):
    return database.execute(
            "SELECT * FROM daily_queue WHERE id=? AND card_id=? AND cycle=? AND status='queued'",
            (queue_entry_id, card_id, cycle),
        ).fetchone()


def light_interval_setting(database):
    return database.execute(
            "SELECT light_first_interval_days FROM settings WHERE id=1",
        ).fetchone()


def complete_queue_entry(database, attempt_state, queue_entry_id):
    database.execute(
            "UPDATE daily_queue SET status='complete',attempt_state=? WHERE id=?",
            (attempt_state, queue_entry_id),
        )


def latest_review_id(database, card_id):
    return database.execute(
                "SELECT MAX(id) FROM reviews WHERE card_id=?", (card_id,),
            ).fetchone()[0]


def finalize_attempt(database, now, result_json, attempt_id):
    database.execute(
        "UPDATE study_attempts SET finalized_at=?,result_json=? WHERE id=?",
        (now, result_json, attempt_id),
    )


def retain_queue_result(database, result_json, queue_entry_id):
    database.execute(
            "UPDATE daily_queue SET review_result_json=? WHERE id=?",
            (result_json, queue_entry_id),
        )


def exercise(database, exercise_id):
    return database.execute(
            "SELECT * FROM study_exercises WHERE id=?", (exercise_id,),
        ).fetchone()


def sibling_exercises(database, study_id, exercise_id, position_id, sibling_group):
    rows = database.execute(
            """SELECT id FROM study_exercises WHERE study_id=? AND id!=?
               AND (position_id=? OR (sibling_group IS NOT NULL AND sibling_group=?))""",
            (study_id, exercise_id, position_id, sibling_group),
        )
    return (row for row in rows)


def bury_sibling(database, sibling_id, day, reason):
    database.execute(
                "INSERT OR IGNORE INTO study_sibling_burials VALUES(?,?,?)",
                (sibling_id, day, reason),
            )


def block_sibling_queue(database, day, sibling_id):
    database.execute(
                """UPDATE daily_queue SET status='blocked' WHERE queue_date=?
                   AND status='queued' AND card_id IN
                   (SELECT id FROM cards WHERE study_exercise_id=?)""",
                (day, sibling_id),
            )


def locked_review_card(database, card_id, exercise_id):
    return database.execute(
        """SELECT * FROM cards WHERE id=? AND study_exercise_id=?
           AND archived=0 AND pending_validation=0 FOR UPDATE""",
        (card_id, exercise_id),
    ).fetchone()


def queue_day(database, queue_entry_id):
    return database.execute(
        "SELECT queue_date FROM daily_queue WHERE id=?", (queue_entry_id,),
    ).fetchone()


def locked_review_entry(database, queue_entry_id, card_id, cycle):
    return database.execute(
        """SELECT * FROM daily_queue WHERE id=? AND card_id=? AND cycle=?
           AND status='queued' FOR UPDATE""",
        (queue_entry_id, card_id, cycle),
    ).fetchone()


def answered_queue_entry(database, queue_entry_id):
    return database.execute(
        "SELECT 1 FROM study_attempts WHERE queue_entry_id=?", (queue_entry_id,),
    ).fetchone()


def latest_effective_review_id(database, card_id):
    return database.execute(
            "SELECT COALESCE(MAX(id),0) FROM reviews WHERE card_id=?", (card_id,),
        ).fetchone()[0]


def lock_attempt_identity(database, attempt_id):
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"tempo:study-attempt:{attempt_id}",),
    )


def attempt(database, attempt_id):
    return database.execute(
        "SELECT * FROM study_attempts WHERE id=?", (attempt_id,),
    ).fetchone()


def exercise_owner(database, exercise_id):
    return database.execute(
            "SELECT study_id FROM study_exercises WHERE id=?", (exercise_id,),
        ).fetchone()


def locked_exercise(database, exercise_id):
    return database.execute(
        "SELECT * FROM study_exercises WHERE id=? FOR UPDATE", (exercise_id,),
    ).fetchone()


def exercise_position(database, position_id):
    return database.execute(
        "SELECT fen FROM study_positions WHERE id=?", (position_id,),
    ).fetchone()


def exercise_revision(database, exercise_id, revision):
    return database.execute(
        "SELECT specification_json FROM study_exercise_revisions WHERE exercise_id=? AND revision=?",
        (exercise_id, revision),
    ).fetchone()


def insert_attempt(database, values):
    database.execute(
        """INSERT INTO study_attempts(id,exercise_id,revision,card_id,queue_entry_id,cycle,context,
           answer_json,answer_hash,assessment_json,assessment_method,grader_version,hint_seen,
           solution_seen_before_answer,started_at,committed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        values,
    )


def pending_attempt(database, attempt_id):
    return database.execute(
        "SELECT card_id,queue_entry_id,context FROM study_attempts WHERE id=?",
        (attempt_id,),
    ).fetchone()


def lock_attempt_card(database, card_id):
    database.execute(
            "SELECT id FROM cards WHERE id=? FOR UPDATE", (card_id,),
        )


def lock_attempt_queue_entry(database, queue_entry_id):
    database.execute(
            "SELECT id FROM daily_queue WHERE id=? FOR UPDATE",
            (queue_entry_id,),
        )


def locked_attempt(database, attempt_id, exercise_id):
    return database.execute(
        "SELECT * FROM study_attempts WHERE id=? AND exercise_id=? FOR UPDATE",
        (attempt_id, exercise_id),
    ).fetchone()
