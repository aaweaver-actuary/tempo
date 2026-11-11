"""Capture identity locks and receipt statements on a borrowed transaction."""

def lock_capture(database, capture_id):
    database.execute_native('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', (f'tempo:tactic-capture:{capture_id}',))

def capture_receipt(database, capture_id):
    return database.execute('SELECT request_json,result_json FROM tactic_captures WHERE id=%s', (capture_id,)).fetchone()

def save_capture_receipt(database, capture_id, card_id, source_kind, source_ref, source_url, note, captured_at, request_json, result_json):
    database.execute('INSERT INTO tactic_captures(id,card_id,source_kind,source_ref,source_url,note,captured_at,\n           request_json,result_json) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)', (capture_id, card_id, source_kind, source_ref, source_url, note, captured_at, request_json, result_json))
