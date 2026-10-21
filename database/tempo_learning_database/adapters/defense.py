"""Existing accepted defense facts and persistence; caller owns decisions/lifecycle."""
from . import postgres_store


def requires_queue_lock():
    return postgres_store.configured()


def lock_card(database, card_id):
    database.execute("SELECT id FROM cards WHERE id=? FOR UPDATE", (card_id,))


def queue_date(database, queue_entry_id):
    return database.execute("SELECT queue_date FROM daily_queue WHERE id=?", (queue_entry_id,)).fetchone()


def prior_attempt(database, attempt_id):
    return database.execute("SELECT * FROM defense_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()


def queued_entry(database, queue_entry_id, card_id):
    return database.execute(
        """SELECT * FROM daily_queue WHERE id=? AND card_id=? AND status='queued'""",
        (queue_entry_id, card_id),
    ).fetchone()


def complete_queue_entry(database, attempt_state, result_json, queue_entry_id):
    database.execute(
        """UPDATE daily_queue SET status='complete',attempt_state=?,review_result_json=?
           WHERE id=?""", (attempt_state, result_json, queue_entry_id),
    )


def next_cycle(database, queue_date, card_id):
    return database.execute(
        "SELECT COALESCE(MAX(cycle),-1)+1 FROM daily_queue WHERE queue_date=? AND card_id=?",
        (queue_date, card_id),
    ).fetchone()[0]


def next_position(database, queue_date):
    return database.execute(
        "SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?", (queue_date,)
    ).fetchone()[0]


def insert_reinforcement(database, queue_date, card_id, cycle, position, attempt_state):
    database.execute(
        """INSERT INTO daily_queue(queue_date,card_id,cycle,position,attempt_state,
                     card_bucket,admission_kind) VALUES(?,?,?,?,?,'defense','review')""",
        (queue_date, card_id, cycle, position, attempt_state),
    )


def insert_attempt(database, attempt_id, candidate_id, card_id, queue_entry_id,
                   revision, move_uci, grade_json, reviewed_at):
    database.execute(
        """INSERT INTO defense_attempts(attempt_id,candidate_id,card_id,
              queue_entry_id,exercise_revision,move_uci,grade_json,reviewed_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (attempt_id, candidate_id, card_id, queue_entry_id, revision, move_uci, grade_json, reviewed_at),
    )


def prior_recognition(database, attempt_id):
    return database.execute(
        """SELECT candidate_id,queue_entry_id,exercise_revision,answer_json
               FROM defense_recognition_submissions WHERE attempt_id=?""", (attempt_id,),
    ).fetchone()


def completed_grade(database, attempt_id):
    return database.execute("SELECT grade_json FROM defense_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()


def source_game(database, game_id):
    return database.execute_native(
        "SELECT definition_json::jsonb->>'source_game_url' game_url FROM accepted_defense_exercises WHERE definition_json::jsonb->>'game_id'=%s LIMIT 1",
        (game_id,),
    ).fetchone()


def recognition_correct(database, attempt_id):
    return database.execute("SELECT recognition_correct FROM defense_recognition_submissions WHERE attempt_id=?", (attempt_id,)).fetchone()[0]


def queued_membership(database, queue_entry_id, card_id):
    return database.execute("SELECT 1 FROM daily_queue WHERE id=? AND card_id=? AND status='queued'", (queue_entry_id, card_id)).fetchone()


def insert_recognition(database, attempt_id, candidate_id, queue_entry_id, revision,
                       answer_json, correct, created_at):
    database.execute(
        """INSERT INTO defense_recognition_submissions(
                 attempt_id,candidate_id,queue_entry_id,exercise_revision,answer_json,
                 recognition_correct,created_at) VALUES(?,?,?,?,?,?,?)""",
        (attempt_id, candidate_id, queue_entry_id, revision, answer_json, correct, created_at),
    )


def light_first_interval_days(database):
    return database.execute("SELECT light_first_interval_days FROM settings WHERE id=1").fetchone()[0]


def bound_recognition(database, attempt_id, candidate_id, queue_entry_id, revision):
    return database.execute(
        """SELECT * FROM defense_recognition_submissions WHERE attempt_id=?
                   AND candidate_id=? AND queue_entry_id=? AND exercise_revision=?""",
        (attempt_id, candidate_id, queue_entry_id, revision),
    ).fetchone()


def insert_analysis_obligation(database, request_id, candidate_id, request_json):
    database.execute(
        "INSERT INTO analysis_obligations(request_id,candidate_id,request_json) VALUES(?,?,?) ON CONFLICT DO NOTHING",
        (request_id, candidate_id, request_json),
    )


def candidate_facts(database, candidate_id):
    return database.execute(
        "SELECT d.*,c.serving_active,c.archived FROM accepted_defense_exercises d JOIN cards c ON c.id=d.card_id WHERE d.candidate_id=?",
        (candidate_id,),
    ).fetchone()
