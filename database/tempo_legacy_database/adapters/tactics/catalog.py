"""Existing catalog progress/activation/introduction statements; no pack selection."""
from .attempts import ensure_tactic_repertoire


def active_pack_rows(database):
    return database.execute('SELECT pack_id FROM tactic_pack_activation WHERE active=1').fetchall()


def progress_rows(database):
    return database.execute('SELECT puzzle_id,clean_pass_at,admitted_at,card_id FROM tactic_progress').fetchall()


def due_tactic_rows(database, day):
    return database.execute("SELECT source_ref FROM cards WHERE content_type='tactic' AND archived=0 AND state IN ('learning','mature') AND due_date<=?", (day,)).fetchall()


def set_activation(database, values):
    database.executemany('INSERT INTO tactic_pack_activation(pack_id,active) VALUES(?,?) ON CONFLICT(pack_id) DO UPDATE SET active=excluded.active', values)


def daily_introduction_limit(database):
    return database.execute('SELECT tactics_new_per_day FROM settings WHERE id=1').fetchone()[0]


def rotation_cursor(database):
    return database.execute('SELECT last_pack_id FROM tactic_rotation WHERE id=1').fetchone()[0]


def introduced_puzzle_rows(database):
    return database.execute('SELECT puzzle_id FROM tactic_progress WHERE admitted_at IS NOT NULL').fetchall()


def last_queue_position(database, day):
    return database.execute('SELECT COALESCE(MAX(position),-1) FROM daily_queue WHERE queue_date=?', (day,)).fetchone()[0]


def create_introduction_card(database, card_id, fen, solution_json, day, puzzle_id, source_fen):
    database.execute("INSERT OR IGNORE INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,scheduling_mode,source_ref,source_fen,introduced_at) VALUES(?,'__tactics__','checkpoint',?,?,'learning',?,'tactic','light',?,?,?)", (card_id, fen, solution_json, day, puzzle_id, source_fen, day))


def save_introduction_progress(database, puzzle_id, pack_id, card_id, day, mode):
    database.execute('INSERT INTO tactic_progress(puzzle_id,deck_id,card_id,admitted_at,admission_mode) VALUES(?,?,?,?,?) ON CONFLICT(puzzle_id) DO UPDATE SET card_id=excluded.card_id,admitted_at=COALESCE(tactic_progress.admitted_at,excluded.admitted_at)', (puzzle_id, pack_id, card_id, day, mode))


def save_introduction(database, puzzle_id, pack_id, day):
    database.execute('INSERT INTO tactic_introductions(puzzle_id,pack_id,introduction_date) VALUES(?,?,?)', (puzzle_id, pack_id, day))


def existing_queue_entry(database, day, card_id):
    return database.execute('SELECT 1 FROM daily_queue WHERE queue_date=? AND card_id=?', (day, card_id)).fetchone()


def append_queue_entry(database, day, card_id, position):
    database.execute('INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,?)', (day, card_id, position))


def save_rotation_cursor(database, cursor):
    database.execute('UPDATE tactic_rotation SET last_pack_id=? WHERE id=1', (cursor,))
