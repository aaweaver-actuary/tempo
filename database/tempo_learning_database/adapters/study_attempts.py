"""Study completion facts and writes, borrowing the caller's transaction."""


def completion_rows(database, attempt):
    card = database.execute(
        "SELECT * FROM cards WHERE id=? AND study_exercise_id=? AND archived=0 AND pending_validation=0 AND serving_active=1",
        (attempt["card_id"], attempt["exercise_id"]),
    ).fetchone()
    queue = database.execute(
        "SELECT * FROM daily_queue WHERE id=? AND card_id=? AND cycle=? AND status='queued'",
        (attempt["queue_entry_id"], attempt["card_id"], attempt["cycle"]),
    ).fetchone()
    return card, queue


def light_interval_days(database):
    return database.execute(
        "SELECT light_first_interval_days FROM settings WHERE id=1",
    ).fetchone()


def complete_queue(database, attempt_state, queue_entry_id):
    database.execute(
        "UPDATE daily_queue SET status='complete',attempt_state=? WHERE id=?",
        (attempt_state, queue_entry_id),
    )


def latest_review_id(database, card_id):
    return database.execute(
        "SELECT MAX(id) FROM reviews WHERE card_id=?",
        (card_id,),
    ).fetchone()[0]


def finalize_attempt(database, finalized_at, result_json, attempt_id):
    database.execute(
        "UPDATE study_attempts SET finalized_at=?,result_json=? WHERE id=?",
        (finalized_at, result_json, attempt_id),
    )


def retain_queue_result(database, result_json, queue_entry_id):
    database.execute(
        "UPDATE daily_queue SET review_result_json=? WHERE id=?",
        (result_json, queue_entry_id),
    )


def exercise(database, exercise_id):
    return database.execute(
        "SELECT * FROM study_exercises WHERE id=?",
        (exercise_id,),
    ).fetchone()


def sibling_exercises(database, exercise):
    siblings = database.execute(
        """SELECT id FROM study_exercises WHERE study_id=? AND id!=?
               AND (position_id=? OR (sibling_group IS NOT NULL AND sibling_group=?))""",
        (
            exercise["study_id"],
            exercise["id"],
            exercise["position_id"],
            exercise["sibling_group"],
        ),
    )
    yield from siblings


def record_sibling_burial(database, exercise_id, study_day, reason):
    database.execute(
        "INSERT OR IGNORE INTO study_sibling_burials VALUES(?,?,?)",
        (exercise_id, study_day, reason),
    )


def block_sibling_queue(database, queue_date, exercise_id):
    database.execute(
        """UPDATE daily_queue SET status='blocked' WHERE queue_date=?
                   AND status='queued' AND card_id IN
                   (SELECT id FROM cards WHERE study_exercise_id=?)""",
        (queue_date, exercise_id),
    )
