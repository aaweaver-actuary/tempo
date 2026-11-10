"""Existing repeat-attempt facts and writes on the caller transaction."""

from tempo_storage.http_composition.availability import available_attempt_sql


def next_cycle(db, day, cid):
    return db.execute(
        'SELECT COALESCE(MAX(cycle),-1)+1 FROM (SELECT cycle FROM daily_queue WHERE queue_date=%s AND card_id=%s UNION ALL SELECT cycle FROM queue_attempt_origins WHERE queue_date=%s AND card_id=%s) cycles',
        (day, cid, day, cid),
    ).fetchone()[0]


def next_position(db, day):
    return db.execute(
                'SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=%s',
                (day,),
            ).fetchone()[0]


def position_at_offset(db, day, miss_reason, offset):
    return db.execute(
            available_attempt_sql(miss_reason).replace("SELECT q.id FROM", "SELECT q.position FROM", 1)
            + ' ORDER BY q.position,q.id LIMIT 1 OFFSET %s',
            (day, offset),
        ).fetchone()


def shift_positions(db, day, position):
    db.execute(
            "UPDATE daily_queue SET position=position+1 WHERE queue_date=%s AND status='queued' AND position>=%s",
            (day, position),
        )


def append_attempt(db, day, cid, cycle, position, attempt):
    return db.execute(
        'INSERT INTO daily_queue(queue_date,card_id,cycle,position,attempt_state) VALUES(%s,%s,%s,%s,%s) RETURNING id',
        (day, cid, cycle, position, attempt),
    ).fetchone()[0]


def set_review_admission(db, inserted_entry_id):
    db.execute(
        "UPDATE daily_queue SET\n               card_bucket=(SELECT content_type FROM cards WHERE id=card_id),\n               admission_kind='review'\n           WHERE id=%s",
        (inserted_entry_id,),
    )
