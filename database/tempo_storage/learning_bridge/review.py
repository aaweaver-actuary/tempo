"""Complete existing legacy review statements and driver-specific lock operations."""
from tempo_storage import postgres
_ATTEMPT_LOCK = ' FOR UPDATE'

def lock_current_card(database: postgres.PostgresConnection, card_id: str) -> None:
    database.execute_native('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (f'tempo:card-edit:{card_id}',))
    database.execute('SELECT id FROM cards WHERE id=%s FOR UPDATE', (card_id,))

def try_lock_continuation(database: postgres.PostgresConnection, card_id: str) -> bool:
    admitted = database.execute_native('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))', (f'tempo:card-edit:{card_id}',)).fetchone()[0]
    if not admitted:
        return False
    row = database.execute('SELECT id FROM cards WHERE id=%s' + ' FOR UPDATE SKIP LOCKED', (card_id,)).fetchone()
    return row is not None

def policy_settings(database):
    return database.execute('SELECT timezone,light_first_interval_days FROM settings WHERE id=1').fetchone()

def receipt(database, attempt_id):
    return database.execute('SELECT card_id,queue_entry_id,outcome,guided,completed_at,result_json,request_json FROM review_attempt_receipts WHERE attempt_id=%s', (attempt_id,)).fetchone()

def card(database, card_id):
    return database.execute('SELECT content_type,archived,superseded_by,revision,state FROM cards WHERE id=%s', (card_id,)).fetchone()

def card_owners(database, card_id):
    return database.execute('SELECT repertoire_id FROM repertoire_cards WHERE card_id=%s UNION SELECT repertoire_id FROM cards WHERE id=%s', (card_id, card_id)).fetchall()

def card_integrity(database, card_id):
    return database.execute("SELECT 1 FROM cards c\n               LEFT JOIN repertoire_cards rc ON rc.card_id=c.id\n               LEFT JOIN repertoires r ON r.id=rc.repertoire_id OR r.id=c.repertoire_id\n               WHERE c.id=%s AND c.archived=0\n                 AND COALESCE(c.pending_validation,0)=0\n                 AND (c.content_type!='opening' OR NOT EXISTS(\n                     SELECT 1 FROM repertoire_integrity_card_blocks block\n                     WHERE block.repertoire_id=r.id AND block.card_id=c.id\n                 )) LIMIT 1", (card_id,)).fetchone()

def attempt_by_id(database, entry_id, card_id, lock):
    return database.execute('SELECT * FROM daily_queue WHERE id=%s AND card_id=%s' + (_ATTEMPT_LOCK if lock else ''), (entry_id, card_id)).fetchone()

def first_queued_attempt(database, day, card_id, lock):
    return database.execute("SELECT * FROM daily_queue WHERE queue_date=%s AND card_id=%s AND status='queued' ORDER BY position,id LIMIT 1" + (_ATTEMPT_LOCK if lock else ''), (day, card_id)).fetchone()

def current_revision(database: postgres.PostgresConnection, card_id: str) -> postgres.TempoRow | None:
    return database.execute('SELECT revision FROM cards WHERE id=%s', (card_id,)).fetchone()

def latest_review_id(database, card_id, valid_only):
    return database.execute(('SELECT COALESCE(MAX(id),0) FROM reviews WHERE card_id=%s' if valid_only else 'SELECT MAX(id) FROM reviews WHERE card_id=%s') + (' AND invalidated_at IS NULL' if valid_only else ''), (card_id,)).fetchone()[0]

def retain_introduction(database: postgres.PostgresConnection, queue_day: str, card_id: str) -> None:
    database.execute('UPDATE cards SET introduced_at=COALESCE(introduced_at,%s) WHERE id=%s', (queue_day, card_id))

def has_later_review(database, card_id, reviewed_at):
    return database.execute('SELECT id FROM reviews WHERE card_id=%s AND invalidated_at IS NULL AND reviewed_at>%s LIMIT 1', (card_id, reviewed_at)).fetchone() is not None

def restore_receipt_outcome(database: postgres.PostgresConnection, outcome: str, guided: int, attempt_id: str | None) -> None:
    database.execute('UPDATE review_attempt_receipts SET outcome=%s,guided=%s WHERE attempt_id=%s', (outcome, guided, attempt_id))

def bind_receipt_request(database: postgres.PostgresConnection, request_json: str, attempt_id: str | None) -> None:
    database.execute('UPDATE review_attempt_receipts SET request_json=%s WHERE attempt_id=%s', (request_json, attempt_id))

def queue_date(database: postgres.PostgresConnection, entry_id: int | None) -> postgres.TempoRow | None:
    return database.execute('SELECT queue_date FROM daily_queue WHERE id=%s', (entry_id,)).fetchone()

def complete_identified_attempt(database: postgres.PostgresConnection, state: str, entry_id: int, card_id: str) -> None:
    database.execute("UPDATE daily_queue SET status='complete',attempt_state=%s WHERE id=%s AND card_id=%s", (state, entry_id, card_id))

def complete_first_attempt(database: postgres.PostgresConnection, day: str, card_id: str) -> None:
    database.execute("UPDATE daily_queue SET status='complete' WHERE id=(SELECT id FROM daily_queue WHERE queue_date=%s AND card_id=%s AND status='queued' ORDER BY position LIMIT 1)", (day, card_id))

def mark_clean_tactic(database: postgres.PostgresConnection, reviewed_at: str, card_id: str) -> None:
    database.execute('UPDATE tactic_progress SET clean_pass_at=COALESCE(clean_pass_at,%s) WHERE card_id=%s', (reviewed_at, card_id))

def insert_receipt(database, attempt_id, card_id, entry_id, outcome, guided, completed_at, review_id, scheduling_status, warning, result_json):
    database.execute('INSERT INTO review_attempt_receipts(attempt_id,card_id,queue_entry_id,outcome,guided,completed_at,review_id,scheduling_status,warning,result_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', (attempt_id, card_id, entry_id, outcome, guided, completed_at, review_id, scheduling_status, warning, result_json))

def retain_completion(database: postgres.PostgresConnection, result_json: str, entry_id: int, card_id: str) -> None:
    database.execute('UPDATE daily_queue SET review_result_json=%s WHERE id=%s AND card_id=%s', (result_json, entry_id, card_id))

def last_queue_id(database, day):
    return database.execute('SELECT COALESCE(MAX(id),0) FROM daily_queue WHERE queue_date=%s', (day,)).fetchone()[0]

def evidence_state(database: postgres.PostgresConnection, attempt_id: str) -> postgres.TempoRow | None:
    return database.execute_native('SELECT state FROM opening_evidence_attempts WHERE attempt_id=%s', (attempt_id,)).fetchone()

def entry_status(database: postgres.PostgresConnection, entry_id: int | None, card_id: str) -> postgres.TempoRow | None:
    return database.execute('SELECT status FROM daily_queue WHERE id=%s AND card_id=%s', (entry_id, card_id)).fetchone()

def inherit_evidence_scope(database, entry_id, snapshot_id, repertoire_id, trained_color):
    database.execute_native('INSERT INTO opening_evidence_queue_contexts(queue_entry_id,presentation_snapshot_id,repertoire_id,effective_trained_color) VALUES(%s,%s,%s,%s) ON CONFLICT(queue_entry_id,presentation_snapshot_id,repertoire_id) DO UPDATE SET effective_trained_color=EXCLUDED.effective_trained_color', (entry_id, snapshot_id, repertoire_id, trained_color))

def continuation_facts(database, entry_id, card_id):
    return database.execute("SELECT q.cycle,q.attempt_failed,c.revision,c.content_type FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE q.id=%s AND q.card_id=%s AND q.status='queued'", (entry_id, card_id)).fetchone()

def save_continuation(database: postgres.PostgresConnection, result_json: str, attempt_id: str | None) -> None:
    database.execute('UPDATE review_attempt_receipts SET result_json=%s WHERE attempt_id=%s', (result_json, attempt_id))
