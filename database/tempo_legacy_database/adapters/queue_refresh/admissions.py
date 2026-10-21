"""Fixed tactical, unseen, due and Study queue-admission operations on borrowed transactions."""
from .eligibility import _DUE_CARD_ELIGIBILITY, _STUDY_NEW_ELIGIBILITY


def lock_tactical_card_edit(database, card_id):
    return database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"tempo:card-edit:{card_id}",))


def tactical_daily_limit(database):
    return database.execute_native("SELECT tactics_new_per_day FROM settings WHERE id=1").fetchone()[0]


def locked_rotation_cursor(database):
    return database.execute_native(
        "SELECT last_pack_id FROM tactic_rotation WHERE id=1 FOR UPDATE",
    ).fetchone()[0]


def pack_activation(database, pack_id):
    return database.execute_native(
        "SELECT active FROM tactic_pack_activation WHERE pack_id=%s",
        (pack_id,),
    ).fetchone()


def locked_puzzle_progress(database, puzzle_id):
    return database.execute_native(
        "SELECT admitted_at FROM tactic_progress WHERE puzzle_id=%s FOR UPDATE",
        (puzzle_id,),
    ).fetchone()


def ensure_tactics_repertoire(database, queue_date):
    return database.execute_native(
        "INSERT INTO repertoires(id,name,source_name,created_at) "
        "VALUES('__tactics__','Tactics','Lichess puzzle database',%s) "
        "ON CONFLICT(id) DO NOTHING",
        (queue_date,),
    )


def insert_tactical_card(database, card_id, training_fen, solution_json, queue_date, puzzle_id, source_fen):
    return database.execute_native(
        "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,"
        "content_type,scheduling_mode,source_ref,source_fen,introduced_at) "
        "VALUES(%s,'__tactics__','checkpoint',%s,%s,'learning',%s,'tactic','light',%s,%s,%s) "
        "ON CONFLICT(id) DO NOTHING",
        (card_id, training_fen, solution_json,
         queue_date, puzzle_id, source_fen, queue_date),
    )


def lock_tactical_card_row(database, card_id):
    return database.execute_native("SELECT id FROM cards WHERE id=%s FOR UPDATE", (card_id,))


def upsert_tactical_progress(database, puzzle_id, pack_id, card_id, queue_date):
    return database.execute_native(
        "INSERT INTO tactic_progress(puzzle_id,deck_id,card_id,admitted_at,admission_mode) "
        "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(puzzle_id) DO UPDATE SET "
        "card_id=excluded.card_id,admitted_at=COALESCE(tactic_progress.admitted_at,excluded.admitted_at)",
        (puzzle_id, pack_id, card_id, queue_date, "light"),
    )


def insert_tactical_introduction(database, puzzle_id, pack_id, queue_date):
    return database.execute_native(
        "INSERT INTO tactic_introductions(puzzle_id,pack_id,introduction_date) VALUES(%s,%s,%s)",
        (puzzle_id, pack_id, queue_date),
    )


def queued_card(database, queue_date, card_id):
    return database.execute_native(
        "SELECT 1 FROM daily_queue WHERE queue_date=%s AND card_id=%s",
        (queue_date, card_id),
    ).fetchone()


def next_queue_position(database, queue_date):
    return database.execute_native(
                "SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=%s",
                (queue_date,),
            ).fetchone()[0]


def append_card(database, queue_date, card_id, position):
    return database.execute_native(
                "INSERT INTO daily_queue(queue_date,card_id,position) VALUES(%s,%s,%s)",
                (queue_date, card_id, position),
            )


def set_rotation_cursor(database, pack_id):
    return database.execute_native(
        "UPDATE tactic_rotation SET last_pack_id=%s WHERE id=1", (pack_id,),
    )


def locked_unseen_entry(database, queue_entry_id, queue_date):
    return database.execute_native(
        """SELECT q.id FROM daily_queue q JOIN cards c ON c.id=q.card_id
           WHERE q.id=%s AND q.queue_date=%s AND q.status='queued'
             AND c.content_type='opening' AND COALESCE(q.admission_kind,'')!='explicit'
             AND c.repertoire_id<>'__retained_cards__'
             AND (c.introduced_at IS NULL OR c.introduced_at=%s)
             AND NOT EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id)
           FOR UPDATE OF q,c""",
        (queue_entry_id, queue_date, queue_date),
    ).fetchone()


def keep_unseen_introduction(database, queue_date, card_id):
    return database.execute_native(
            "UPDATE cards SET introduced_at=%s,state='learning' WHERE id=%s",
            (queue_date, card_id),
        )


def remove_unseen_entry(database, queue_entry_id):
    return database.execute_native("DELETE FROM daily_queue WHERE id=%s", (queue_entry_id,))


def reset_unseen_introduction(database, card_id):
    return database.execute_native(
        "UPDATE cards SET introduced_at=NULL,state='new' WHERE id=%s",
        (card_id,),
    )


def locked_card(database, card_id):
    return database.execute_native(
        "SELECT id FROM cards WHERE id=%s FOR UPDATE", (card_id,),
    ).fetchone()


def due_card_eligibility(database, card_id, queue_date):
    return database.execute_native(
        "SELECT 1 FROM cards c WHERE c.id=%s AND " + _DUE_CARD_ELIGIBILITY,
        (card_id, queue_date, queue_date, queue_date),
    ).fetchone()


def study_daily_allowance(database):
    return database.execute_native(
        "SELECT study_new_per_day FROM settings WHERE id=1",
    ).fetchone()[0]


def admitted_study_count(database, queue_date):
    return database.execute_native(
        """SELECT COUNT(*) FROM daily_queue queue JOIN cards card ON card.id=queue.card_id
           WHERE queue.queue_date=%s AND card.content_type='study_exercise'
             AND card.state='new' AND queue.status IN ('queued','complete','buried')""",
        (queue_date,),
    ).fetchone()[0]


def study_card_eligibility(database, card_id, queue_date):
    return database.execute_native(
        "SELECT 1 FROM cards c JOIN study_exercises exercise ON exercise.id=c.study_exercise_id "
        "JOIN studies study ON study.id=exercise.study_id WHERE c.id=%s AND "
        + _STUDY_NEW_ELIGIBILITY,
        (card_id, queue_date, queue_date, queue_date),
    ).fetchone()


def append_study_card(database, queue_date, card_id, position):
    return database.execute_native(
        """INSERT INTO daily_queue(queue_date,card_id,position,card_bucket,admission_kind)
           VALUES(%s,%s,%s,'study_exercise','new')""",
        (queue_date, card_id, position),
    )
