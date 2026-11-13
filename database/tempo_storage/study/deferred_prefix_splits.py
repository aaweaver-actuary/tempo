"""Borrowed-transaction storage for accepted Study prefix-split intents."""


def try_lock_source(database, card_id):
    """Use the same advisory-then-row order as Study issuance without waiting."""
    acquired = database.execute_native(
        "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))", (f"tempo:card-edit:{card_id}",),
    ).fetchone()[0]
    if not acquired:
        return "busy", None
    card = database.execute_native(
        "SELECT id,revision FROM cards WHERE id=%s FOR UPDATE SKIP LOCKED", (card_id,),
    ).fetchone()
    if card is not None:
        return "locked", card
    exists = database.execute_native("SELECT 1 FROM cards WHERE id=%s", (card_id,)).fetchone() is not None
    return ("busy" if exists else "missing"), None


def issued_origin(database, card_id, revision, entry_id):
    return database.execute_native(
        "SELECT 1 FROM queue_attempt_origins WHERE card_id=%s AND revision=%s AND queue_entry_id=%s AND issued_as_head=1",
        (card_id, revision, entry_id),
    ).fetchone() is not None


def create_intent(database, intent_id, card_id, revision, entry_id):
    database.execute_native(
        """INSERT INTO study_deferred_prefix_splits(intent_id,card_id,source_revision,queue_entry_id,state)
           VALUES(%s,%s,%s,%s,'waiting_review')
           ON CONFLICT(card_id,source_revision,queue_entry_id) DO NOTHING""",
        (intent_id, card_id, revision, entry_id),
    )
    return database.execute_native(
        "SELECT * FROM study_deferred_prefix_splits WHERE card_id=%s AND source_revision=%s AND queue_entry_id=%s",
        (card_id, revision, entry_id),
    ).fetchone()


def intent(database, intent_id, *, lock=False, skip_locked=False):
    return database.execute_native(
        "SELECT * FROM study_deferred_prefix_splits WHERE intent_id=%s" + (" FOR UPDATE" + (" SKIP LOCKED" if skip_locked else "") if lock else ""),
        (intent_id,),
    ).fetchone()


def due_intent(database, after):
    return database.execute_native(
        """SELECT intent_id,card_id FROM study_deferred_prefix_splits
           WHERE state IN ('waiting_review','ready') AND next_check_at<=NOW() AND intent_id>%s
           ORDER BY intent_id LIMIT 1""", (after,),
    ).fetchone()


def delay_check(database, intent_id):
    database.execute_native(
        "UPDATE study_deferred_prefix_splits SET next_check_at=NOW()+INTERVAL '60 seconds',updated_at=NOW() WHERE intent_id=%s AND state IN ('waiting_review','ready')",
        (intent_id,),
    )


def delay_check_if_unlocked(database, intent_id):
    database.execute_native(
        """UPDATE study_deferred_prefix_splits SET next_check_at=NOW()+INTERVAL '5 seconds',updated_at=NOW()
           WHERE intent_id=(SELECT intent_id FROM study_deferred_prefix_splits
             WHERE intent_id=%s AND state IN ('waiting_review','ready') FOR UPDATE SKIP LOCKED)""",
        (intent_id,),
    )


def matching_reviews(database, card_id, revision, entry_id):
    return database.execute_native(
        """SELECT receipt.attempt_id,receipt.review_id,receipt.result_json
           FROM review_attempt_receipts receipt
           JOIN reviews review ON review.id=receipt.review_id AND review.card_id=receipt.card_id
           JOIN queue_attempt_origins origin ON origin.queue_entry_id=receipt.queue_entry_id
             AND origin.card_id=receipt.card_id AND origin.revision=%s AND origin.issued_as_head=1
           WHERE receipt.card_id=%s AND receipt.queue_entry_id=%s
             AND review.invalidated_at IS NULL AND review.source_kind IN ('study','position_study')
             AND review.source_ref='attempt:' || receipt.attempt_id
           ORDER BY receipt.attempt_id LIMIT 2""",
        (revision, card_id, entry_id),
    ).fetchall()


def set_ready(database, intent_id, attempt_id, review_id, child_operation_id):
    return database.execute_native(
        """UPDATE study_deferred_prefix_splits SET state='ready',review_attempt_id=%s,review_id=%s,
           child_operation_id=%s,reason=NULL,updated_at=NOW() WHERE intent_id=%s AND state='waiting_review'""",
        (attempt_id, review_id, child_operation_id, intent_id),
    ).rowcount == 1


def set_held(database, intent_id, reason):
    return database.execute_native(
        "UPDATE study_deferred_prefix_splits SET state='held',reason=%s,updated_at=NOW() WHERE intent_id=%s AND state IN ('waiting_review','ready')",
        (reason, intent_id),
    ).rowcount == 1


def set_applied(database, intent_id, result_json):
    return database.execute_native(
        """UPDATE study_deferred_prefix_splits SET state='applied',result_json=%s::jsonb,reason=NULL,updated_at=NOW()
           WHERE intent_id=%s AND state='ready'""",
        (result_json, intent_id),
    ).rowcount == 1
