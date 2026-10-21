"""Existing six fixed Discovery acceptance queries; application composes scopes and tasks."""


def matching_intent(database, choice, *, lock):
    return database.execute_native(
        "SELECT id,state FROM discovery_admission_intents "
            "WHERE opportunity_id=%s AND selected_move_uci=%s AND evidence_fingerprint=%s"
            + (" FOR UPDATE" if lock else ""),
        (choice.opportunity_id, choice.selected_move_uci, choice.evidence_fingerprint),
    ).fetchone()


def opportunity_owner(database, values):
    return database.execute_native(
        "SELECT repertoire_id FROM repertoire_opportunities WHERE id=%s",
        values,
    ).fetchone()


def locked_opportunity(database, values):
    return database.execute_native(
        "SELECT * FROM repertoire_opportunities WHERE id=%s FOR UPDATE",
        values,
    ).fetchone()


def intent_task_state(database, values):
    return database.execute_native(
        "SELECT state FROM background_tasks WHERE kind='discovery_admission' "
            "AND deduplication_key=%s",
        values,
    ).fetchone()


def insert_intent(database, values):
    return database.execute_native(
        "INSERT INTO discovery_admission_intents("
            "id,opportunity_id,repertoire_id,evidence_fingerprint,starting_fen,"
            "selected_move_uci,preview_moves_json,recommendation_json,line_id,created_at,updated_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        values,
    )


def mark_opportunity_preparing(database, values):
    return database.execute_native(
        "UPDATE repertoire_opportunities SET admission_state='preparing',"
            "seen_at=COALESCE(seen_at,%s),updated_at=%s WHERE id=%s",
        values,
    )
