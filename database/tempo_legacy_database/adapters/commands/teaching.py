"""Teaching-state idempotent insert/replay on the caller's existing card lock."""


def active_card_for_update(database, card_id: str):
    return database.execute(
        "SELECT 1 FROM cards WHERE id=? AND archived=0 FOR UPDATE", (card_id,),
    ).fetchone()


def record_teaching_timestamp(database, card_id: str, revision: int, ply: int, taught_at: str) -> str:
    inserted_state = database.execute(
        "INSERT INTO teaching_states(card_id,revision,ply,taught_at) VALUES(?,?,?,?) "
        "ON CONFLICT(card_id,revision,ply) DO NOTHING RETURNING taught_at",
        (card_id, revision, ply, taught_at),
    ).fetchone()
    if inserted_state is None:
        inserted_state = database.execute(
            "SELECT taught_at FROM teaching_states WHERE card_id=? AND revision=? AND ply=?",
            (card_id, revision, ply),
        ).fetchone()
    return inserted_state[0]
