"""Learning-owned capture card and queue statements on a borrowed transaction."""
from tempo_storage.queue_position_lock import lock_queue_date_for_position

def ensure_capture_repertoire(database, repertoire_id, name, source_name, created_at):
    database.execute('INSERT INTO repertoires(id,name,source_name,created_at,is_main) VALUES(%s,%s,%s,%s,0) ON CONFLICT DO NOTHING', (repertoire_id, name, source_name, created_at))

def create_capture_card(database, card_id, repertoire_id, starting_fen, moves_json, due_date, source_ref, source_fen, trained_color, introduced_at):
    return database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,\n           content_type,scheduling_mode,source_ref,source_fen,trained_color,introduced_at)\n           VALUES(%s,%s,'checkpoint',%s,%s,'learning',%s,'tactic','normal',%s,%s,%s,%s)\n           ON CONFLICT(id) DO NOTHING", (card_id, repertoire_id, starting_fen, moves_json, due_date, source_ref, source_fen, trained_color, introduced_at)).rowcount == 1

def capture_card(database, card_id):
    return database.execute('SELECT * FROM cards WHERE id=%s' + ' FOR UPDATE', (card_id,)).fetchone()

def link_capture_card(database, repertoire_id, card_id):
    database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)', (repertoire_id, card_id))

def reopen_capture_card(database, due_date, card_id):
    database.execute("UPDATE cards SET state='learning',due_date=%s,scheduling_mode=CASE WHEN scheduling_mode='light' THEN 'normal' ELSE scheduling_mode END WHERE id=%s", (due_date, card_id))

def lock_capture_queue(database, queue_date):
    lock_queue_date_for_position(database, queue_date)

def mark_capture_guided(database, queue_date, card_id):
    database.execute("UPDATE daily_queue SET attempt_state='guided' WHERE queue_date=%s AND card_id=%s AND status='queued'", (queue_date, card_id))
