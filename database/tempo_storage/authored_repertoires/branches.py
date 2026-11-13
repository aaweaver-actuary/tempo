"""Existing native authored-branch locks/projections/writes, without chess policy."""


def lock_repertoire_for_add(database, repertoire_id):
    return database.execute_native(
        "SELECT 1 FROM repertoires WHERE id=%s FOR UPDATE", (repertoire_id,),
    ).fetchone()


def insert_line_values(database, values):
    return database.execute_native(
        "INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING RETURNING id",
        values,
    ).fetchone()


def initial_training_depth(database):
    return database.execute_native(
        "SELECT initial_depth FROM settings WHERE id=1"
    ).fetchone()[0]


def set_line_training_depth(database, line_id, depth):
    database.execute_native(
        "INSERT INTO repertoire_line_training_depths(line_id,learner_decision_count) "
        "VALUES(%s,%s) ON CONFLICT(line_id) DO UPDATE SET "
        "learner_decision_count=excluded.learner_decision_count",
        (line_id, depth),
    )


def lock_repertoire_for_remove(database, repertoire_id):
    return database.execute_native(
        "SELECT id FROM repertoires WHERE id=%s FOR UPDATE", (repertoire_id,),
    ).fetchone()


def stored_repertoire_lines(database, repertoire_id):
    return database.execute_native(
        "SELECT id,start_fen,moves_json FROM repertoire_lines "
        "WHERE repertoire_id=%s ORDER BY id", (repertoire_id,),
    ).fetchall()


def delete_matching_lines(database, matching_line_ids):
    database.execute_native(
        "DELETE FROM repertoire_lines WHERE id=ANY(%s::text[])",
        (matching_line_ids,),
    )
