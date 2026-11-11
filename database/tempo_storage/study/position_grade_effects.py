"""Fixed PostgreSQL operations for position grade receipts.

The application validates the facts and owns the borrowed transaction.
"""


def locked_owner(database, parameters=()):
    return database.execute_native("""SELECT o.epoch, o.mode, e.source_digest
             FROM study_position_owners o
             JOIN study_position_epochs e ON e.repertoire_id=o.repertoire_id AND e.epoch=o.epoch
            WHERE o.repertoire_id=%s FOR SHARE OF o,e SKIP LOCKED""", parameters)


def locked_state(database, parameters=()):
    return database.execute_native("""SELECT repertoire_id,variant,position,expected_uci,policy_version,
                      owner_epoch,source_digest,version,fsrs_internal_card_id,fsrs_json,due_at
                 FROM study_position_states
                WHERE repertoire_id=%s AND variant=%s AND position=%s
                  FOR UPDATE SKIP LOCKED""", parameters)


def issued_plan(database, parameters=()):
    return database.execute_native("""SELECT attempt_id,queue_entry_id,card_id,card_revision,repertoire_id,variant,
                  owner_epoch,source_digest,issue_study_day,plan_digest,plan_json
             FROM study_position_issued_plans WHERE attempt_id=%s""", parameters)


def first_response(database, parameters=()):
    return database.execute_native("""SELECT event_id,attempt_id,decision_index,repertoire_id,variant,position,owner_epoch,
                      payload_digest,payload_json,observed_at,accepted_at
                 FROM study_position_first_responses WHERE event_id=%s""", parameters)


def saved_completion(database, parameters=()):
    return database.execute_native("""SELECT 1 FROM opening_evidence_attempts e
             JOIN review_attempt_receipts r ON r.attempt_id=e.attempt_id
             JOIN reviews v ON v.id=r.review_id
            WHERE e.attempt_id=%s AND e.queue_entry_id=%s AND e.card_id=%s AND e.card_revision=%s
              AND e.state='complete' AND e.review_id=%s
              AND r.queue_entry_id=e.queue_entry_id AND r.card_id=e.card_id
              AND r.review_id=e.review_id AND r.scheduling_status='canonical'
              AND v.card_id=e.card_id AND v.source_kind IN ('study','position_study') AND v.source_ref=%s
              AND v.invalidated_at IS NULL""", parameters)


def prior_grade(database, parameters=()):
    return database.execute_native("""SELECT effect_id,event_id,grade,accepted_study_day,accepted_at,fsrs_before_json,
                      fsrs_after_json,version_before,version_after,request_digest
                 FROM study_position_grades WHERE event_id=%s OR
                  (attempt_id=%s AND decision_index=%s AND effect_kind='first_response')""", parameters)


def daily_good_grades(database, parameters=()):
    return database.execute_native("""SELECT event_id,attempt_id,decision_index,grade,accepted_study_day
                 FROM study_position_grades WHERE repertoire_id=%s AND variant=%s AND position=%s
                   AND grade='good' AND accepted_study_day=%s""", parameters)


def update_schedule(database, parameters=()):
    return database.execute_native("""UPDATE study_position_states SET fsrs_json=%s,due_at=%s,version=%s
                     WHERE repertoire_id=%s AND variant=%s AND position=%s AND version=%s""", parameters)


def insert_grade(database, parameters=()):
    return database.execute_native("""INSERT INTO study_position_grades
                (effect_id,source_kind,event_id,attempt_id,decision_index,effect_kind,repertoire_id,
                 variant,position,owner_epoch,expected_uci,policy_version,source_digest,accepted_study_day,
                 grade,request_digest,accepted_at,fsrs_before_json,fsrs_after_json,version_before,version_after)
                VALUES (%s,'study',%s,%s,%s,'first_response',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", parameters)
