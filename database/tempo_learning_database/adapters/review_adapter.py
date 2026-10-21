"""Complete legacy-review storage operations borrowing the caller's transaction."""
from .postgres_store import PostgresConnection


_ATTEMPT_LOCK = " FOR UPDATE"


def lock_current_review_card(database, card_id):
    if hasattr(database, "execute_native"):
        database.execute_native(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (f"tempo:card-edit:{card_id}",),
        )
    database.execute(
        "SELECT id FROM cards WHERE id=? FOR UPDATE", (card_id,)
    )


def try_lock_continuation_card(database, card_id):
    if isinstance(database, PostgresConnection):
        admitted = database.execute_native(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))",
            (f"tempo:card-edit:{card_id}",),
        ).fetchone()[0]
        if not admitted:
            return False
    row = database.execute(
        "SELECT id FROM cards WHERE id=?"
        + (
            " FOR UPDATE SKIP LOCKED"
            if isinstance(database, PostgresConnection)
            else ""
        ),
        (card_id,),
    ).fetchone()
    return row is not None


def review_policy(database):
    return database.execute(
        "SELECT timezone,light_first_interval_days FROM settings WHERE id=1"
    ).fetchone()


def attempt_receipt(database, attempt_id):
    return database.execute(
        "SELECT card_id,queue_entry_id,outcome,guided,completed_at,result_json,request_json "
        "FROM review_attempt_receipts WHERE attempt_id=?",
        (attempt_id,),
    ).fetchone()


def card(database, card_id):
    return database.execute(
        "SELECT content_type,archived,superseded_by,revision,state FROM cards WHERE id=?",
        (card_id,),
    ).fetchone()


def card_owners(database, card_id):
    return tuple(
        item[0]
        for item in database.execute(
            "SELECT repertoire_id FROM repertoire_cards WHERE card_id=? UNION SELECT repertoire_id FROM cards WHERE id=?",
            (card_id, card_id),
        ).fetchall()
    )


def card_integrity(database, card_id):
    return database.execute(
        """SELECT 1 FROM cards c
               LEFT JOIN repertoire_cards rc ON rc.card_id=c.id
               LEFT JOIN repertoires r ON r.id=rc.repertoire_id OR r.id=c.repertoire_id
               WHERE c.id=? AND c.archived=0
                 AND COALESCE(c.pending_validation,0)=0
                 AND (c.content_type!='opening' OR NOT EXISTS(
                     SELECT 1 FROM repertoire_integrity_card_blocks block
                     WHERE block.repertoire_id=r.id AND block.card_id=c.id
                 )) LIMIT 1""",
        (card_id,),
    ).fetchone()


def serving_active(database, card_id):
    return database.execute(
        "SELECT serving_active FROM cards WHERE id=?", (card_id,)
    ).fetchone()[0]


def attempt_by_id(database, queue_entry_id, card_id):
    return database.execute(
        "SELECT * FROM daily_queue WHERE id=? AND card_id=?" + _ATTEMPT_LOCK,
        (queue_entry_id, card_id),
    ).fetchone()


def first_queued_attempt(database, queue_day, card_id):
    return database.execute(
        "SELECT * FROM daily_queue WHERE queue_date=? AND card_id=? AND status='queued' ORDER BY position,id LIMIT 1"
        + _ATTEMPT_LOCK,
        (queue_day, card_id),
    ).fetchone()


def current_revision(database, card_id):
    return database.execute(
        "SELECT revision FROM cards WHERE id=?", (card_id,)
    ).fetchone()


def latest_review_id(database, card_id, valid_only):
    return database.execute(
        (
            "SELECT COALESCE(MAX(id),0) FROM reviews WHERE card_id=?"
            if valid_only
            else "SELECT MAX(id) FROM reviews WHERE card_id=?"
        )
        + (" AND invalidated_at IS NULL" if valid_only else ""),
        (card_id,),
    ).fetchone()[0]


def retain_introduction(database, queue_day, card_id):
    database.execute(
        "UPDATE cards SET introduced_at=COALESCE(introduced_at,?) WHERE id=?",
        (queue_day, card_id),
    )


def later_review(database, card_id, reviewed_at):
    return database.execute(
        "SELECT id FROM reviews WHERE card_id=? AND invalidated_at IS NULL AND reviewed_at>? LIMIT 1",
        (card_id, reviewed_at),
    ).fetchone()


def restore_receipt_outcome(database, outcome, guided, attempt_id):
    database.execute(
        "UPDATE review_attempt_receipts SET outcome=?,guided=? WHERE attempt_id=?",
        (outcome, guided, attempt_id),
    )


def bind_receipt_request(database, request_json, attempt_id):
    database.execute(
        "UPDATE review_attempt_receipts SET request_json=? WHERE attempt_id=?",
        (request_json, attempt_id),
    )


def queue_date(database, queue_entry_id):
    return database.execute("SELECT queue_date FROM daily_queue WHERE id=?", (queue_entry_id,)).fetchone()


def complete_identified_attempt(database, attempt_state, queue_entry_id, card_id):
    database.execute(
        "UPDATE daily_queue SET status='complete',attempt_state=? WHERE id=? AND card_id=?",
        (attempt_state, queue_entry_id, card_id),
    )


def complete_first_queued_attempt(database, queue_day, card_id):
    database.execute(
        "UPDATE daily_queue SET status='complete' WHERE id=(SELECT id FROM daily_queue WHERE queue_date=? AND card_id=? AND status='queued' ORDER BY position LIMIT 1)",
        (queue_day, card_id),
    )


def mark_clean_tactic(database, reviewed_at, card_id):
    database.execute(
        "UPDATE tactic_progress SET clean_pass_at=COALESCE(clean_pass_at,?) WHERE card_id=?",
        (reviewed_at, card_id),
    )


def save_receipt(database, *, attempt_id, card_id, queue_entry_id, outcome, guided, completed_at,
                 review_id, scheduling_status, warning, result_json):
    database.execute(
        "INSERT INTO review_attempt_receipts(attempt_id,card_id,queue_entry_id,outcome,guided,completed_at,review_id,scheduling_status,warning,result_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (attempt_id, card_id, queue_entry_id, outcome, guided, completed_at, review_id,
         scheduling_status, warning, result_json),
    )


def retain_daily_completion(database, result_json, queue_entry_id, card_id):
    database.execute(
        "UPDATE daily_queue SET review_result_json=? WHERE id=? AND card_id=?",
        (result_json, queue_entry_id, card_id),
    )


def maximum_queue_id(database, queue_day):
    return database.execute(
        "SELECT COALESCE(MAX(id),0) FROM daily_queue WHERE queue_date=?", (queue_day,)
    ).fetchone()[0]


def record_priority_refresh(database, repertoire_id):
    database.execute(
        "INSERT INTO advisory_notifications(kind,subject) VALUES('priority_refresh',?) ON CONFLICT(kind,subject) DO UPDATE SET generation=advisory_notifications.generation+1",
        (repertoire_id,),
    )


def completed_evidence_state(database, attempt_id):
    return database.execute_native(
        "SELECT state FROM opening_evidence_attempts WHERE attempt_id=%s",
        (attempt_id,),
    ).fetchone()


def evidence_revision_facts(database, card_id, queue_entry_id):
    revision = database.execute(
        "SELECT revision FROM cards WHERE id=?", (card_id,)
    ).fetchone()
    entry = database.execute(
        "SELECT status FROM daily_queue WHERE id=? AND card_id=?",
        (queue_entry_id, card_id),
    ).fetchone()
    return revision, entry


def inherit_evidence_scope(database, queue_entry_id, snapshot_id, repertoire_id, trained_color):
    database.execute_native(
        "INSERT INTO opening_evidence_queue_contexts(queue_entry_id,presentation_snapshot_id,repertoire_id,effective_trained_color) VALUES(%s,%s,%s,%s) ON CONFLICT(queue_entry_id,presentation_snapshot_id,repertoire_id) DO UPDATE SET effective_trained_color=EXCLUDED.effective_trained_color",
        (queue_entry_id, snapshot_id, repertoire_id, trained_color),
    )


def continuation_facts(database, queue_entry_id, card_id):
    return database.execute(
        "SELECT q.cycle,q.attempt_failed,c.revision,c.content_type FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE q.id=? AND q.card_id=? AND q.status='queued'",
        (queue_entry_id, card_id),
    ).fetchone()


def save_continuation(database, result_json, attempt_id):
    database.execute(
        "UPDATE review_attempt_receipts SET result_json=? WHERE attempt_id=?",
        (result_json, attempt_id),
    )
