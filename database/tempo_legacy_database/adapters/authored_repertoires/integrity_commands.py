"""Exact authored-line repair queries on the existing caller transaction."""
from .branches import lock_repertoire_for_add as locked_repertoire


def insert_replacement_line(database, values):
    database.execute_native(
        "INSERT INTO repertoire_lines("
        "id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING",
        values,
    )


def copy_training_depth(database, new_line_id, source_line_id):
    database.execute_native(
        "INSERT INTO repertoire_line_training_depths(line_id,learner_decision_count) "
        "SELECT %s,learner_decision_count FROM repertoire_line_training_depths "
        "WHERE line_id=%s ON CONFLICT(line_id) DO UPDATE SET "
        "learner_decision_count=GREATEST("
        "repertoire_line_training_depths.learner_decision_count,"
        "excluded.learner_decision_count)",
        (new_line_id, source_line_id),
    )


def delete_replaced_line(database, source_line_id):
    database.execute_native(
        "DELETE FROM repertoire_lines WHERE id=%s", (source_line_id,),
    )


def locked_issue_signature(database, issue_id, repertoire_id):
    return database.execute_native(
        "SELECT signature FROM repertoire_integrity_issues "
        "WHERE id=%s AND repertoire_id=%s FOR UPDATE",
        (issue_id, repertoire_id),
    ).fetchone()


def locked_source_line(database, source_id, repertoire_id):
    return database.execute_native(
            "SELECT * FROM repertoire_lines WHERE id=%s AND repertoire_id=%s FOR UPDATE",
            (source_id, repertoire_id),
        ).fetchone()


def locked_source_card(database, source_id):
    return database.execute_native(
            "SELECT * FROM cards WHERE id=%s FOR UPDATE", (source_id,),
        ).fetchone()


def repertoire_card_link(database, repertoire_id, source_id):
    return database.execute_native(
            "SELECT 1 FROM repertoire_cards WHERE repertoire_id=%s AND card_id=%s",
            (repertoire_id, source_id),
        ).fetchone()
