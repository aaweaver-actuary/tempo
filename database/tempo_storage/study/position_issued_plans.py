"""Fixed PostgreSQL operations for position plan issuance.

The application validates the facts and owns the borrowed transaction.
"""


def issued_plan(database, parameters=()):
    return database.execute_native("SELECT * FROM study_position_issued_plans WHERE attempt_id=%s", parameters)


def lock_card(database, parameters=()):
    return database.execute_native("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))", parameters)


def locked_card(database, parameters=()):
    return database.execute_native("SELECT * FROM cards WHERE id=%s FOR UPDATE SKIP LOCKED", parameters)


def locked_day(database, parameters=()):
    return database.execute_native("SELECT queue_date FROM daily_queue_days WHERE queue_date=%s FOR UPDATE SKIP LOCKED", parameters)


def locked_queue(database, parameters=()):
    return database.execute_native("SELECT * FROM daily_queue WHERE id=%s FOR UPDATE SKIP LOCKED", parameters)


def origin_index_exists(database, parameters=()):
    return database.execute_native("""SELECT EXISTS (
        SELECT 1 FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid
        WHERE t.oid='study_position_issued_plans'::regclass AND i.indisunique
          AND i.indisvalid AND i.indpred IS NULL AND i.indexprs IS NULL AND i.indnkeyatts=3
          AND (SELECT array_agg(a.attname::text ORDER BY k.ordinality)
               FROM unnest(i.indkey) WITH ORDINALITY k(attnum,ordinality)
               JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=k.attnum
               WHERE k.ordinality<=i.indnkeyatts) =
              ARRAY['queue_entry_id','card_id','card_revision']::text[])""", parameters)


def issued_for_entry(database, parameters=()):
    return database.execute_native("""SELECT * FROM study_position_issued_plans
        WHERE queue_entry_id=%s AND card_id=%s AND card_revision=%s LIMIT 2""", parameters)


def origin_count(database, parameters=()):
    return database.execute_native("SELECT count(*) FROM queue_attempt_origins WHERE queue_entry_id=%s", parameters)


def locked_origins(database, parameters=()):
    return database.execute_native("""SELECT * FROM queue_attempt_origins WHERE queue_entry_id=%s
        ORDER BY card_id,revision LIMIT 2 FOR UPDATE SKIP LOCKED""", parameters)


def lock_attempt(database, parameters=()):
    return database.execute_native("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))", parameters)


def attempt_count(database, parameters=()):
    return database.execute_native("SELECT count(*) FROM opening_evidence_attempts WHERE attempt_id=%s", parameters)


def locked_attempt(database, parameters=()):
    return database.execute_native("""SELECT card_id,card_revision,queue_entry_id,repertoire_id
        FROM opening_evidence_attempts WHERE attempt_id=%s FOR UPDATE SKIP LOCKED""", parameters)


def review_receipt(database, parameters=()):
    return database.execute_native("SELECT 1 FROM review_attempt_receipts WHERE attempt_id=%s", parameters)


def good_today(database, parameters=()):
    return database.execute_native("""SELECT 1 FROM study_position_grades
        WHERE repertoire_id=%s AND variant=%s AND position=%s AND accepted_study_day=%s
          AND grade='good' AND owner_epoch=%s AND version_after=%s LIMIT 1""", parameters)


def insert_plan(database, parameters=()):
    return database.execute_native("""INSERT INTO study_position_issued_plans
        (attempt_id,queue_entry_id,card_id,card_revision,repertoire_id,variant,owner_epoch,
         source_digest,issue_study_day,plan_digest,plan_json,issued_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
        ON CONFLICT (attempt_id) DO NOTHING RETURNING attempt_id""", parameters)


def saved_completion(database, parameters=(), *, lock=False):
    return database.execute_native("""SELECT a.card_id,a.card_revision,a.queue_entry_id,a.repertoire_id,
        a.state,a.contiguous_sequence,a.terminal_json,a.review_id,a.retired_operation_id,
        receipt.card_id receipt_card,receipt.queue_entry_id receipt_queue,receipt.review_id receipt_review,
        receipt.outcome,receipt.guided,r.card_id review_card,r.source_kind,r.source_ref,r.invalidated_at
        FROM opening_evidence_attempts a JOIN review_attempt_receipts receipt ON receipt.attempt_id=a.attempt_id
        JOIN reviews r ON r.id=receipt.review_id WHERE a.attempt_id=%s""" +
        (" FOR SHARE OF a,receipt,r SKIP LOCKED" if lock else ""), parameters)


def first_responses(database, parameters=(), *, lock=False):
    return database.execute_native("""SELECT * FROM study_position_first_responses
            WHERE attempt_id=%s ORDER BY decision_index LIMIT 21""" +
            (" FOR SHARE SKIP LOCKED" if lock else ""), parameters)


def again_index_exists(database, parameters=()):
    return database.execute_native("""SELECT EXISTS (SELECT 1 FROM pg_index i
            WHERE i.indexrelid=to_regclass('study_position_recent_again')
              AND i.indrelid='study_position_grades'::regclass AND i.indisvalid)""", parameters)


def again_after_review(database, parameters=()):
    return database.execute_native("""SELECT 1 FROM study_position_grades again
            JOIN study_position_states state ON state.repertoire_id=again.repertoire_id
             AND state.variant=again.variant AND state.position=again.position
            JOIN review_attempt_receipts prior_review ON prior_review.attempt_id=%s
            WHERE again.repertoire_id=%s AND again.variant=%s AND again.position=%s
              AND again.owner_epoch=%s AND again.accepted_study_day=%s AND again.grade='again'
              AND again.expected_uci=%s AND again.policy_version=%s
              AND state.owner_epoch=again.owner_epoch AND again.version_before >= %s
              AND again.version_after > %s AND again.version_after <= state.version AND prior_review.completed_at IS NOT NULL
              AND again.accepted_at > prior_review.completed_at::timestamptz
            LIMIT 1""", parameters)
