"""Bounded opening-attempt evidence reads; JSON decoding stays after the read scope."""


def opening_attempt_row(database, attempt_id: str):
    return database.execute_native('SELECT * FROM opening_evidence_attempts WHERE attempt_id=%s', (attempt_id,)).fetchone()


def opening_event_payloads(database, attempt_id: str) -> list[str]:
    return [row[0] for row in database.execute_native('SELECT event_json FROM opening_evidence_events WHERE attempt_id=%s ORDER BY sequence LIMIT 256', (attempt_id,)).fetchall()]
