"""Existing capture locks/materialization/receipt statements on a borrowed transaction."""
from ..queue_position_lock import lock_queue_date_for_position


def lock_capture(database, capture_id):
    if hasattr(database, "execute_native"):
        database.execute_native(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"tempo:tactic-capture:{capture_id}",),
        )


def capture_receipt(database, capture_id):
    return database.execute(
        "SELECT request_json,result_json FROM tactic_captures WHERE id=?", (capture_id,),
    ).fetchone()


def ensure_capture_repertoire(database, repertoire_id, name, source_name, created_at):
    database.execute(
        "INSERT OR IGNORE INTO repertoires(id,name,source_name,created_at,is_main) VALUES(?,?,?,?,0)",
        (repertoire_id, name, source_name, created_at),
    )


def create_capture_card(database, card_id, repertoire_id, starting_fen, moves_json, due_date,
                        source_ref, source_fen, trained_color, introduced_at):
    return database.execute(
        """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
           content_type,scheduling_mode,source_ref,source_fen,trained_color,introduced_at)
           VALUES(?,?,'checkpoint',?,?,'learning',?,'tactic','normal',?,?,?,?)
           ON CONFLICT(id) DO NOTHING""",
        (card_id, repertoire_id, starting_fen, moves_json, due_date,
         source_ref, source_fen, trained_color, introduced_at),
    ).rowcount == 1


def capture_card(database, card_id):
    return database.execute(
        "SELECT * FROM cards WHERE id=?" + (" FOR UPDATE" if hasattr(database, "execute_native") else ""),
        (card_id,),
    ).fetchone()


def link_capture_card(database, repertoire_id, card_id):
    database.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)", (repertoire_id, card_id))


def reopen_capture_card(database, due_date, card_id):
    database.execute(
        "UPDATE cards SET state='learning',due_date=?,"
        "scheduling_mode=CASE WHEN scheduling_mode='light' THEN 'normal' ELSE scheduling_mode END WHERE id=?",
        (due_date, card_id),
    )


def lock_capture_queue(database, queue_date):
    if hasattr(database, "execute_native"):
        lock_queue_date_for_position(database, queue_date)


def mark_capture_guided(database, queue_date, card_id):
    database.execute(
        "UPDATE daily_queue SET attempt_state='guided' WHERE queue_date=? AND card_id=? AND status='queued'",
        (queue_date, card_id),
    )


def save_capture_receipt(database, capture_id, card_id, source_kind, source_ref, source_url,
                         note, captured_at, request_json, result_json):
    database.execute(
        """INSERT INTO tactic_captures(id,card_id,source_kind,source_ref,source_url,note,captured_at,
           request_json,result_json) VALUES(?,?,?,?,?,?,?,?,?)""",
        (capture_id, card_id, source_kind, source_ref, source_url, note, captured_at, request_json, result_json),
    )
