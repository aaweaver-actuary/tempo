"""Existing repeat-attempt facts and writes on the caller transaction."""

from tempo_legacy_database.adapters.http_composition.availability import available_attempt_sql


def next_cycle(db, day, cid):
    return db.execute(
        "SELECT COALESCE(MAX(cycle),-1)+1 FROM ("
        "SELECT cycle FROM daily_queue WHERE queue_date=? AND card_id=? UNION ALL "
        "SELECT cycle FROM queue_attempt_origins WHERE queue_date=? AND card_id=?) cycles",
        (day, cid, day, cid),
    ).fetchone()[0]


def next_position(db, day):
    return db.execute(
                "SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?",
                (day,),
            ).fetchone()[0]


def position_at_offset(db, day, miss_reason, offset):
    return db.execute(
            available_attempt_sql(miss_reason).replace("SELECT q.id FROM", "SELECT q.position FROM", 1)
            + " ORDER BY q.position,q.id LIMIT 1 OFFSET ?",
            (day, offset),
        ).fetchone()


def shift_positions(db, day, position):
    db.execute(
            "UPDATE daily_queue SET position=position+1 WHERE queue_date=? AND status='queued' AND position>=?",
            (day, position),
        )


def append_attempt(db, day, cid, cycle, position, attempt):
    return db.execute(
        "INSERT INTO daily_queue(queue_date,card_id,cycle,position,attempt_state) "
        "VALUES(?,?,?,?,?) RETURNING id",
        (day, cid, cycle, position, attempt),
    ).fetchone()[0]


def set_review_admission(db, inserted_entry_id):
    db.execute(
        """UPDATE daily_queue SET
               card_bucket=(SELECT content_type FROM cards WHERE id=card_id),
               admission_kind='review'
           WHERE id=?""",
        (inserted_entry_id,),
    )
