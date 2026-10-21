"""Existing review schedule/queue facts and writes on the caller's transaction."""


def valid_defense_reviews(database, card_id):
    return database.execute(
        """SELECT rating,reviewed_at FROM reviews WHERE card_id=? AND invalidated_at IS NULL
           ORDER BY reviewed_at,id""",
        (card_id,),
    ).fetchall()


def reset_defense_schedule(database, due_date, card_id):
    database.execute(
        """UPDATE cards SET due_date=?,interval_days=0,fsrs_card_json=NULL,
               first_correct_at=NULL,reinforcement_pending=0,stability=0,
               guided_review=0,state='learning',scheduling_mode='normal',
               hard_correct_streak=0,recent_attempts_json='[]' WHERE id=? AND content_type='defense'""",
        (due_date, card_id),
    )


def write_defense_schedule(database, *, due_date, interval_days, fsrs_card_json,
                           first_correct_at, reinforcement_pending, stability, state,
                           scheduling_mode, hard_correct_streak, recent_attempts_json, card_id):
    database.execute(
        """UPDATE cards SET due_date=?,interval_days=?,fsrs_card_json=?,first_correct_at=?,
           reinforcement_pending=?,stability=?,guided_review=0,state=?,scheduling_mode=?,
           hard_correct_streak=?,recent_attempts_json=? WHERE id=? AND content_type='defense'""",
        (due_date, interval_days, fsrs_card_json, first_correct_at, reinforcement_pending,
         stability, state, scheduling_mode, hard_correct_streak, recent_attempts_json, card_id),
    )


def queue_order_entries(database, queue_date):
    return database.execute(
        """SELECT id,card_id FROM daily_queue
           WHERE queue_date=? AND status='queued' ORDER BY id""",
        (queue_date,),
    ).fetchall()


def saved_order_membership(database, queue_date):
    return database.execute("SELECT membership_hash FROM daily_queue_days WHERE queue_date=?", (queue_date,)).fetchone()


def retain_daily_order(database, queue_date, seed, membership_hash, generated_at):
    database.execute(
        """INSERT INTO daily_queue_days(queue_date,seed,membership_hash,generated_at)
           VALUES(?,?,?,?) ON CONFLICT(queue_date) DO UPDATE SET
           membership_hash=excluded.membership_hash,generated_at=excluded.generated_at""",
        (queue_date, seed, membership_hash, generated_at),
    )


def latest_review_id(database, card_id):
    return database.execute(
        "SELECT MAX(id) FROM reviews WHERE card_id=?", (card_id,)
    ).fetchone()[0]


def queued_entry(database, day, card_id):
    return database.execute(
        "SELECT id FROM daily_queue WHERE queue_date=? AND card_id=? AND status='queued' ORDER BY position,id LIMIT 1",
        (day, card_id),
    ).fetchone()


def next_cycle(database, day, card_id):
    return database.execute(
        "SELECT COALESCE(MAX(cycle),-1)+1 FROM (SELECT cycle FROM daily_queue WHERE queue_date=? AND card_id=? UNION ALL SELECT cycle FROM queue_attempt_origins WHERE queue_date=? AND card_id=?) cycles",
        (day, card_id, day, card_id),
    ).fetchone()[0]


def append_position(database, day):
    return database.execute(
        "SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?",
        (day,),
    ).fetchone()[0]


def insert_queued_entry(database, day, card_id, cycle, position, attempt_state, priority_reason):
    return database.execute(
        "INSERT INTO daily_queue(queue_date,card_id,cycle,position,attempt_state,gameplay_priority_reason) VALUES(?,?,?,?,?,?) RETURNING id",
        (day, card_id, cycle, position, attempt_state, priority_reason),
    ).fetchone()


def retain_priority_reason(database, priority_reason, entry_id):
    database.execute(
        "UPDATE daily_queue SET gameplay_priority_reason=? WHERE id=?",
        (priority_reason, entry_id),
    )


def priority_queue_rows(database, workspace_id, day):
    from tempo_learning_remediation import current_miss_sql

    return database.execute_native(
        """SELECT q.id,q.position,q.card_id,q.gameplay_priority_reason,q.admission_repertoire_id,
                      card.revision,card.publication_id,card.publication_generation,
                      CASE WHEN q.gameplay_priority_reason IS NOT NULL THEN """
        + current_miss_sql("card")
        + """
                      ELSE FALSE END AS current_miss
               FROM daily_queue q JOIN cards card ON card.id=q.card_id
               WHERE q.queue_date=%s AND q.status='queued' ORDER BY q.position,q.id""",
        (workspace_id, day),
    ).fetchall()


def update_queue_positions(database, updates):
    database.executemany(
        "UPDATE daily_queue SET position=? WHERE id=?", updates
    )


def refresh_entry_metadata(database, entry_id):
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


def other_queued_ids(database, day, entry_id):
    return [
        row[0]
        for row in database.execute(
            "SELECT id FROM daily_queue WHERE queue_date=? AND status='queued' AND id!=? ORDER BY position,id",
            (day, entry_id),
        )
    ]


def set_queue_position(database, position, entry_id):
    database.execute(
        "UPDATE daily_queue SET position=? WHERE id=?", (position, entry_id)
    )
