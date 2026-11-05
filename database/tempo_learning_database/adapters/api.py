"""Existing Learning HTTP read projections and command statements."""
import psycopg
from psycopg_pool import PoolTimeout
from . import queue_application


def begin_reconciliation(database):
    database.execute_native("SAVEPOINT review_reconciliation")


def rollback_reconciliation(database):
    database.execute_native("ROLLBACK TO SAVEPOINT review_reconciliation")


def release_reconciliation(database):
    database.execute_native("RELEASE SAVEPOINT review_reconciliation")


def active_endgame_template(database, template_id):
    return database.execute(
        "SELECT * FROM endgame_templates WHERE id=? AND enabled=1", (template_id,)
    ).fetchone()


def lock_endgame_card(database, card_id):
    return database.execute(
        "SELECT serving_active FROM cards WHERE id=? FOR UPDATE", (card_id,)
    ).fetchone()


def endgame_enabled(database, template_id):
    return database.execute("SELECT enabled FROM endgame_templates WHERE id=?", (template_id,)).fetchone()


def insert_endgame_attempt(database, attempt_id, template_id, fen, target, created_at):
    database.execute(
        "INSERT INTO endgame_attempts(id,template_id,start_fen,target,created_at) VALUES(?,?,?,?,?)",
        (attempt_id, template_id, fen, target, created_at),
    )


def light_first_interval_days(database):
    return database.execute("SELECT light_first_interval_days FROM settings WHERE id=1").fetchone()[0]


def migration_state(database):
    from tempo_learning_database.ddl.migrate import expected_migrations
    applied = dict(database.execute_native("SELECT id,checksum FROM migration_history").fetchall())
    return applied, expected_migrations()


def queue_entry_state(database, entry_id):
    return database.execute(
        "SELECT status,attempt_failed,queue_date FROM daily_queue WHERE id=?", (entry_id,)
    ).fetchone()


def available_head(database, day, miss_reason):
    return database.execute(
        queue_application.available_attempt_sql(miss_reason)
        + " ORDER BY q.position,q.id LIMIT 1", (day,),
    ).fetchone()


def opening_attempt(database, attempt_id):
    return database.execute("SELECT * FROM opening_evidence_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()


def opening_events(database, attempt_id):
    return database.execute(
        "SELECT event_json FROM opening_evidence_events WHERE attempt_id=? ORDER BY sequence LIMIT 256", (attempt_id,)
    ).fetchall()


def published_study_exercise(database, exercise_id, study_id):
    return database.execute(
        "SELECT e.*,p.fen,r.specification_json FROM study_exercises e JOIN study_positions p ON p.id=e.position_id JOIN study_exercise_revisions r ON r.exercise_id=e.id AND r.revision=e.current_revision WHERE e.id=? AND e.study_id=? AND e.status='published'",
        (exercise_id, study_id),
    ).fetchone()


def committed_study_specification(database, attempt_id, exercise_id, study_id):
    return database.execute(
        "SELECT r.specification_json FROM study_attempts a JOIN study_exercises e ON e.id=a.exercise_id JOIN study_exercise_revisions r ON r.exercise_id=e.id AND r.revision=a.revision WHERE a.id=? AND e.id=? AND e.study_id=?",
        (attempt_id, exercise_id, study_id),
    ).fetchone()


def operation_intent(database, operation_id):
    return database.execute('SELECT command_name,payload_json FROM operation_receipts WHERE operation_id=?', (operation_id,)).fetchone()
