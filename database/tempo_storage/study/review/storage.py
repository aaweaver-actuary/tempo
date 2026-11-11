"""Existing review/queue statements; no connection or transaction ownership."""
import json

def valid_defense_reviews(database, card_id):
    return database.execute('SELECT rating,reviewed_at FROM reviews WHERE card_id=%s AND invalidated_at IS NULL\n           ORDER BY reviewed_at,id', (card_id,)).fetchall()

def reset_defense_schedule(database, card_id, due_date):
    database.execute("UPDATE cards SET due_date=%s,interval_days=0,fsrs_card_json=NULL,\n               first_correct_at=NULL,reinforcement_pending=0,stability=0,\n               guided_review=0,state='learning',scheduling_mode='normal',\n               hard_correct_streak=0,recent_attempts_json='[]' WHERE id=%s AND content_type='defense'", (due_date(), card_id))

def save_defense_schedule(database, card_id, schedule, state):
    database.execute("UPDATE cards SET due_date=%s,interval_days=%s,fsrs_card_json=%s,first_correct_at=%s,\n           reinforcement_pending=%s,stability=%s,guided_review=0,state=%s,scheduling_mode=%s,\n           hard_correct_streak=%s,recent_attempts_json=%s WHERE id=%s AND content_type='defense'", (schedule.due_date.isoformat(), schedule.interval_days, schedule.fsrs_card_json, schedule.first_correct_at, int(schedule.reinforcement_pending), schedule.stability, state, schedule.scheduling_mode, schedule.hard_correct_streak, json.dumps(schedule.recent_attempts), card_id))

def queued_order_entries(database, queue_date):
    return database.execute("SELECT id,card_id FROM daily_queue\n           WHERE queue_date=%s AND status='queued' ORDER BY id", (queue_date,)).fetchall()

def queue_membership_receipt(database, queue_date):
    return database.execute('SELECT membership_hash FROM daily_queue_days WHERE queue_date=%s', (queue_date,)).fetchone()

def write_queue_order_receipt(database, queue_date, seed, membership_hash, generated_at):
    database.execute('INSERT INTO daily_queue_days(queue_date,seed,membership_hash,generated_at)\n           VALUES(%s,%s,%s,%s) ON CONFLICT(queue_date) DO UPDATE SET\n           membership_hash=excluded.membership_hash,generated_at=excluded.generated_at', (queue_date, seed, membership_hash, generated_at()))

def queued_entry(database, day, card_id):
    return database.execute("SELECT id FROM daily_queue WHERE queue_date=%s AND card_id=%s AND status='queued' ORDER BY position,id LIMIT 1", (day, card_id)).fetchone()

def next_cycle(database, day, card_id):
    return database.execute('SELECT COALESCE(MAX(cycle),-1)+1 FROM daily_queue WHERE queue_date=%s AND card_id=%s', (day, card_id)).fetchone()[0]

def insert_entry(database, day, card_id, cycle, protect_presented, attempt_state, priority_reason):
    return database.execute('INSERT INTO daily_queue(queue_date,card_id,cycle,position,attempt_state,gameplay_priority_reason) VALUES(%s,%s,%s,%s,%s,%s) RETURNING id', (day, card_id, cycle, database.execute('SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=%s', (day,)).fetchone()[0] if protect_presented else 2000000000, attempt_state, priority_reason)).fetchone()

def set_priority(database, priority_reason, entry_id):
    database.execute('UPDATE daily_queue SET gameplay_priority_reason=%s WHERE id=%s', (priority_reason, entry_id))

def other_queued_entries(database, day, entry_id):
    return database.execute("SELECT id FROM daily_queue WHERE queue_date=%s AND status='queued' AND id!=%s ORDER BY position,id", (day, entry_id))

def set_position(database, position, entry_id):
    database.execute('UPDATE daily_queue SET position=%s WHERE id=%s', (position, entry_id))

def classify_entry(database, entry_id):
    from tempo_storage.queue_refresh.application.admission import retained_generated_card_sql
    database.execute("UPDATE daily_queue SET\n               card_bucket=(SELECT content_type FROM cards WHERE id=card_id),\n               admission_kind=CASE WHEN admission_kind='explicit' THEN 'explicit'\n                   WHEN EXISTS(SELECT 1 FROM reviews review\n                   WHERE review.card_id=daily_queue.card_id AND review.source_kind='study'\n                     AND review.invalidated_at IS NULL) OR (" + retained_generated_card_sql('daily_queue.card_id') + ") THEN 'review' ELSE 'new' END\n           WHERE id=%s", (entry_id,))

class PriorityPositionConflict(RuntimeError):
    pass

def apply_priority_positions(database, day, changes):
    """One guarded borrowed write for only the positions changed by the owner."""
    if not changes:
        return
    changed = database.execute_native("UPDATE daily_queue queue SET position=planned.position\n        FROM unnest(%s::bigint[],%s::bigint[],%s::bigint[])\n            AS planned(id,previous,position)\n        WHERE queue.id=planned.id AND queue.queue_date=%s AND queue.status='queued'\n          AND queue.position=planned.previous\n          AND queue.review_result_json IS NULL\n          AND queue.admission_kind IS DISTINCT FROM 'explicit'\n          AND NOT EXISTS(SELECT 1 FROM queue_attempt_origins origin\n              WHERE origin.queue_entry_id=queue.id AND origin.card_id=queue.card_id\n                AND origin.revision=(SELECT revision FROM cards WHERE id=queue.card_id)\n                AND origin.issued_as_head=1)\n          AND NOT EXISTS(SELECT 1 FROM opening_evidence_attempts attempt\n              WHERE attempt.queue_entry_id=queue.id)", ([row[0] for row in changes], [row[1] for row in changes], [row[2] for row in changes], day)).rowcount
    if changed != len(changes):
        raise PriorityPositionConflict('priority positions changed or an attempt became protected')
from tempo_storage.queue_refresh.presentation import priority_ownership_state
