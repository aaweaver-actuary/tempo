"""Learning teaching-state SQL and existing card lock on the caller's transaction."""


def teaching_rows(database, card_id):
    return database.execute(
        "SELECT card_id,revision,ply,taught_at FROM teaching_states "
        "WHERE card_id=? ORDER BY revision,ply", (card_id,)).fetchall()


def lock_teaching_card(database, card_id):
    database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                            (f"tempo:card-edit:{card_id}",))
    card = database.execute("SELECT * FROM cards WHERE id=? FOR UPDATE", (card_id,)).fetchone()
    return card


def record_teaching_state(database, card_id, revision, ply, taught_at):
    database.execute(
        "INSERT INTO teaching_states(card_id,revision,ply,taught_at) VALUES(?,?,?,?) "
        "ON CONFLICT(card_id,revision,ply) DO NOTHING",
        (card_id, revision, ply, taught_at))
    return database.execute(
        "SELECT * FROM teaching_states WHERE card_id=? AND revision=? AND ply=?",
        (card_id, revision, ply)).fetchone()
