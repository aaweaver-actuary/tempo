"""Existing packaged/historical tactic-attempt statements; no grading policy."""


def lock_puzzle_attempt(database, puzzle_id):
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"tempo:tactic-puzzle:{puzzle_id}",),
    )


def attempt_receipt(database, attempt_id):
    return database.execute(
        "SELECT result_json FROM tactic_discovery_attempts WHERE id=?", (attempt_id,),
    ).fetchone()


def first_light_interval(database):
    return database.execute("SELECT light_first_interval_days FROM settings WHERE id=1").fetchone()[0]


def ensure_tactic_repertoire(database, created_at):
    database.execute(
        "INSERT OR IGNORE INTO repertoires(id,name,source_name,created_at) "
        "VALUES('__tactics__','Tactics','Lichess puzzle database',?)", (created_at,),
    )


def save_progress(database, puzzle_id, pack_id, card_id, clean_at, admitted_at, mode):
    database.execute(
        "INSERT INTO tactic_progress(puzzle_id,deck_id,card_id,clean_pass_at,admitted_at,admission_mode) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(puzzle_id) DO UPDATE SET "
        "clean_pass_at=COALESCE(tactic_progress.clean_pass_at,excluded.clean_pass_at),"
        "card_id=excluded.card_id,admitted_at=COALESCE(tactic_progress.admitted_at,excluded.admitted_at),"
        "admission_mode=excluded.admission_mode",
        (puzzle_id, pack_id, card_id, clean_at, admitted_at, mode),
    )


def create_attempt_card(database, card_id, training_fen, solution_json, due_date,
                        content_type, scheduling_mode, puzzle_id, source_fen, introduced_at):
    database.execute(
        "INSERT OR IGNORE INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,"
        "content_type,scheduling_mode,source_ref,source_fen,state,introduced_at) "
        "VALUES(?, '__tactics__','checkpoint',?,?,?,?,?,?,?,'learning',?)",
        (card_id, training_fen, solution_json, due_date, content_type, scheduling_mode,
         puzzle_id, source_fen, introduced_at),
    )


def lock_attempt_card(database, card_id):
    database.execute("SELECT id FROM cards WHERE id=? FOR UPDATE", (card_id,))


def reopen_attempt_card(database, due_date, card_id):
    database.execute(
        "UPDATE cards SET state='learning',"
        "scheduling_mode=CASE WHEN scheduling_mode='light' THEN 'normal' ELSE scheduling_mode END,"
        "due_date=? WHERE id=?", (due_date, card_id),
    )


def queued_attempt_card(database, queue_date, card_id):
    return database.execute(
        "SELECT 1 FROM daily_queue WHERE queue_date=? AND card_id=? AND status='queued'",
        (queue_date, card_id),
    ).fetchone()


def attempt_card_schedule(database, card_id):
    return database.execute("SELECT scheduling_mode,due_date FROM cards WHERE id=?", (card_id,)).fetchone()


def save_admission_mode(database, mode, puzzle_id):
    database.execute("UPDATE tactic_progress SET admission_mode=? WHERE puzzle_id=?", (mode, puzzle_id))


def save_attempt_receipt(database, attempt_id, deck_id, puzzle_id, clean, result_json):
    database.execute("INSERT INTO tactic_discovery_attempts VALUES(?,?,?,?,?)",
                     (attempt_id, deck_id, puzzle_id, clean, result_json))
