"""Existing review/queue statements; no connection or transaction ownership."""
import json
import sqlite3


def valid_defense_reviews(database, card_id):
    return database.execute(
        """SELECT rating,reviewed_at FROM reviews WHERE card_id=? AND invalidated_at IS NULL
           ORDER BY reviewed_at,id""", (card_id,),
    ).fetchall()


def reset_defense_schedule(database, card_id, due_date):
    database.execute(
        """UPDATE cards SET due_date=?,interval_days=0,fsrs_card_json=NULL,
               first_correct_at=NULL,reinforcement_pending=0,stability=0,
               guided_review=0,state='learning',scheduling_mode='normal',
               hard_correct_streak=0,recent_attempts_json='[]' WHERE id=? AND content_type='defense'""",
        (due_date(), card_id),
    )


def save_defense_schedule(database, card_id, schedule, state):
    database.execute(
        """UPDATE cards SET due_date=?,interval_days=?,fsrs_card_json=?,first_correct_at=?,
           reinforcement_pending=?,stability=?,guided_review=0,state=?,scheduling_mode=?,
           hard_correct_streak=?,recent_attempts_json=? WHERE id=? AND content_type='defense'""",
        (schedule.due_date.isoformat(), schedule.interval_days, schedule.fsrs_card_json,
         schedule.first_correct_at, int(schedule.reinforcement_pending), schedule.stability,
         state, schedule.scheduling_mode, schedule.hard_correct_streak,
         json.dumps(schedule.recent_attempts), card_id),
    )


def queued_order_entries(database, queue_date):
    return database.execute(
        """SELECT id,card_id FROM daily_queue
           WHERE queue_date=? AND status='queued' ORDER BY id""",
        (queue_date,),
    ).fetchall()


def queue_membership_receipt(database, queue_date):
    return database.execute("SELECT membership_hash FROM daily_queue_days WHERE queue_date=?", (queue_date,)).fetchone()


def write_queue_order_receipt(database, queue_date, seed, membership_hash, generated_at):
    database.execute(
        """INSERT INTO daily_queue_days(queue_date,seed,membership_hash,generated_at)
           VALUES(?,?,?,?) ON CONFLICT(queue_date) DO UPDATE SET
           membership_hash=excluded.membership_hash,generated_at=excluded.generated_at""",
        (queue_date, seed, membership_hash, generated_at()),
    )


def queued_entry(database, day, card_id):
    return database.execute(
        "SELECT id FROM daily_queue WHERE queue_date=? AND card_id=? AND status='queued' ORDER BY position,id LIMIT 1",
        (day, card_id),
    ).fetchone()


def next_cycle(database, day, card_id):
    return database.execute(
        "SELECT COALESCE(MAX(cycle),-1)+1 FROM daily_queue WHERE queue_date=? AND card_id=?",
        (day, card_id),
    ).fetchone()[0]


def insert_entry(database, day, card_id, cycle, protect_presented, attempt_state, priority_reason):
    return database.execute(
        "INSERT INTO daily_queue(queue_date,card_id,cycle,position,attempt_state,gameplay_priority_reason) VALUES(?,?,?,?,?,?) RETURNING id",
        (day, card_id, cycle,
         database.execute("SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?", (day,)).fetchone()[0]
         if protect_presented else 2_000_000_000, attempt_state, priority_reason),
    ).fetchone()


def set_priority(database, priority_reason, entry_id):
    database.execute(
        "UPDATE daily_queue SET gameplay_priority_reason=? WHERE id=?",
        (priority_reason, entry_id),
    )


def other_queued_entries(database, day, entry_id):
    return database.execute(
        "SELECT id FROM daily_queue WHERE queue_date=? AND status='queued' AND id!=? ORDER BY position,id",
        (day, entry_id),
    )


def set_position(database, position, entry_id):
    database.execute("UPDATE daily_queue SET position=? WHERE id=?", (position, entry_id))


def classify_entry(database, entry_id):
    database.execute(
        """UPDATE daily_queue SET
               card_bucket=(SELECT content_type FROM cards WHERE id=card_id),
               admission_kind=CASE WHEN admission_kind='explicit' THEN 'explicit'
                   WHEN EXISTS(SELECT 1 FROM reviews review
                   WHERE review.card_id=daily_queue.card_id AND review.source_kind='study'
                     AND review.invalidated_at IS NULL) THEN 'review' ELSE 'new' END
           WHERE id=?""",
        (entry_id,),
    )


class PriorityPositionConflict(RuntimeError):
    pass


def apply_priority_positions(database, day, changes):
    """One guarded borrowed write for only the positions changed by the owner."""
    from tempo_legacy_database.adapters.postgres_store import PostgresConnection
    if not changes:
        return
    if not isinstance(database, PostgresConnection):
        for entry_id,previous,position in changes:
            set_position(database,position,entry_id)
        return
    changed = database.execute_native("""UPDATE daily_queue queue SET position=planned.position
        FROM unnest(%s::bigint[],%s::bigint[],%s::bigint[])
            AS planned(id,previous,position)
        WHERE queue.id=planned.id AND queue.queue_date=%s AND queue.status='queued'
          AND queue.position=planned.previous AND queue.attempt_failed=0
          AND queue.review_result_json IS NULL
          AND queue.admission_kind IS DISTINCT FROM 'explicit'
          AND queue.attempt_state IN ('clean','unstarted','gameplay')
          AND NOT EXISTS(SELECT 1 FROM queue_attempt_origins origin
              WHERE origin.queue_entry_id=queue.id AND origin.card_id=queue.card_id
                AND origin.revision=(SELECT revision FROM cards WHERE id=queue.card_id)
                AND origin.issued_as_head=1)
          AND NOT EXISTS(SELECT 1 FROM opening_evidence_attempts attempt
              WHERE attempt.queue_entry_id=queue.id)""",
        ([row[0] for row in changes],[row[1] for row in changes],
         [row[2] for row in changes],day)).rowcount
    if changed != len(changes):
        raise PriorityPositionConflict('priority positions changed or an attempt became protected')


from tempo_legacy_database.adapters.queue_refresh.presentation import priority_ownership_state
