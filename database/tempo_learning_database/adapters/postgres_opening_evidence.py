"""Existing bounded opening-evidence SQL/resources; application policy lives in runtime."""
from __future__ import annotations

from psycopg.errors import SerializationFailure
from tempo_learning_database.adapters import postgres_store


def configured():
    return postgres_store.configured()


def _presentation_snapshot(database, snapshot_id, queue_entry_id, repertoire_id):
    row = database.execute_native(
        "SELECT snapshot.*,context.effective_trained_color FROM opening_evidence_presentations snapshot "
        "JOIN opening_evidence_queue_contexts context ON context.presentation_snapshot_id=snapshot.id "
        "WHERE snapshot.id=%s AND context.queue_entry_id=%s AND context.repertoire_id=%s",
        (snapshot_id, queue_entry_id, repertoire_id),
    ).fetchone()
    return dict(row) if row else None


def presentation_snapshot(snapshot_id, queue_entry_id, repertoire_id):
    with postgres_store.connection(read_only=True) as database:
        snapshot = _presentation_snapshot(database, snapshot_id, queue_entry_id, repertoire_id)
    return snapshot


def queue_snapshots(candidates):
    with postgres_store.connection(read_only=True) as database:
        rows = database.execute_native(
            "SELECT context.queue_entry_id,context.repertoire_id,context.effective_trained_color,snapshot.* "
            "FROM opening_evidence_queue_contexts context JOIN opening_evidence_presentations snapshot "
            "ON snapshot.id=context.presentation_snapshot_id JOIN daily_queue queue ON queue.id=context.queue_entry_id "
            "JOIN cards card ON card.id=queue.card_id WHERE context.queue_entry_id=ANY(%s::bigint[]) "
            "AND snapshot.card_id=card.id AND snapshot.revision=card.revision "
            "AND snapshot.start_fen=card.start_fen AND snapshot.moves_json=card.moves_json "
            "AND snapshot.trained_color IS NOT DISTINCT FROM card.trained_color "
            "AND (queue.admission_repertoire_id IS NULL OR context.repertoire_id=queue.admission_repertoire_id) "
            "AND EXISTS(SELECT 1 FROM repertoires eligible WHERE eligible.id=context.repertoire_id "
            "AND (eligible.id=card.repertoire_id OR EXISTS(SELECT 1 FROM repertoire_cards link "
            "WHERE link.card_id=card.id AND link.repertoire_id=eligible.id))) "
            "AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block "
            "WHERE block.card_id=card.id AND block.repertoire_id=context.repertoire_id)",
            ([card["queue_entry_id"] for card in candidates],),
        ).fetchall()
        snapshots = [dict(row) for row in rows]
    return snapshots


def prior_observation(database, attempt_id, decision_index):
    return database.execute_native(
        "SELECT observation_json FROM opening_evidence_observations WHERE attempt_id=%s AND decision_index=%s",
        (attempt_id, decision_index),
    ).fetchone()


def add_summary_counts(database, decision_id, increments):
    database.execute_native(
        "INSERT INTO opening_evidence_summaries(decision_id,first_responses,clean_successes,incorrect_responses,"
        "assisted_responses,manual_failures,corrections) VALUES(%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(decision_id) DO UPDATE SET "
        "first_responses=opening_evidence_summaries.first_responses+EXCLUDED.first_responses,"
        "clean_successes=opening_evidence_summaries.clean_successes+EXCLUDED.clean_successes,"
        "incorrect_responses=opening_evidence_summaries.incorrect_responses+EXCLUDED.incorrect_responses,"
        "assisted_responses=opening_evidence_summaries.assisted_responses+EXCLUDED.assisted_responses,"
        "manual_failures=opening_evidence_summaries.manual_failures+EXCLUDED.manual_failures,"
        "corrections=opening_evidence_summaries.corrections+EXCLUDED.corrections",
        (decision_id, *increments),
    )


def insert_clean_day(database, decision_id, study_day):
    return database.execute_native(
        "INSERT INTO opening_evidence_clean_days(decision_id,study_day) VALUES(%s,%s) "
        "ON CONFLICT DO NOTHING RETURNING study_day",
        (decision_id, study_day),
    ).fetchone()


def increment_clean_days(database, decision_id):
    database.execute_native(
        "UPDATE opening_evidence_summaries SET distinct_clean_days=distinct_clean_days+1 WHERE decision_id=%s",
        (decision_id,),
    )


def upsert_observation(database, attempt_id, decision_index, decision_id, observed_at, failure_at, observation_json):
    database.execute_native(
        "INSERT INTO opening_evidence_observations(attempt_id,decision_index,decision_id,observed_at,failure_at,observation_json) "
        "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(attempt_id,decision_index) DO UPDATE SET "
        "observed_at=EXCLUDED.observed_at,failure_at=EXCLUDED.failure_at,observation_json=EXCLUDED.observation_json",
        (attempt_id, decision_index, decision_id, observed_at, failure_at, observation_json),
    )


def lock_card_identity(database, card_id):
    database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"tempo:card-edit:{card_id}",))


def current_card_for_update(database, card_id):
    return database.execute_native(
        "SELECT revision,archived,serving_active FROM cards WHERE id=%s FOR UPDATE", (card_id,)
    ).fetchone()


def completed_aggregate(database, attempt_id, card_id):
    return database.execute_native(
        "SELECT 1 FROM review_attempt_receipts receipt JOIN opening_evidence_attempts attempt "
        "ON attempt.attempt_id=receipt.attempt_id WHERE receipt.attempt_id=%s "
        "AND receipt.card_id=%s AND attempt.state='complete'", (attempt_id, card_id),
    ).fetchone()


def retired_operation(database, attempt_id):
    return database.execute_native(
        "SELECT retired_operation_id FROM opening_evidence_attempts WHERE attempt_id=%s", (attempt_id,)
    ).fetchone()


def presentation_scope(database, queue_entry_id, snapshot_id, repertoire_id, card_id, revision, trained_color):
    return database.execute_native(
        "SELECT 1 FROM opening_evidence_queue_contexts context JOIN opening_evidence_presentations snapshot "
        "ON snapshot.id=context.presentation_snapshot_id WHERE context.queue_entry_id=%s "
        "AND context.presentation_snapshot_id=%s AND context.repertoire_id=%s "
        "AND snapshot.card_id=%s AND snapshot.revision=%s AND context.effective_trained_color=%s",
        (queue_entry_id, snapshot_id, repertoire_id, card_id, revision, trained_color),
    ).fetchone()


def queue_scope(database, queue_entry_id, snapshot_id, repertoire_id):
    return database.execute_native(
        "SELECT 1 FROM opening_evidence_queue_contexts WHERE queue_entry_id=%s AND presentation_snapshot_id=%s AND repertoire_id=%s",
        (queue_entry_id, snapshot_id, repertoire_id),
    ).fetchone()


def parent_aggregate(database, attempt_id, snapshot_id, repertoire_id):
    return database.execute_native(
        "SELECT receipt.card_id,receipt.queue_entry_id,receipt.result_json FROM review_attempt_receipts receipt "
        "WHERE receipt.attempt_id=%s AND EXISTS(SELECT 1 FROM opening_evidence_queue_contexts context "
        "WHERE context.queue_entry_id=receipt.queue_entry_id AND context.presentation_snapshot_id=%s "
        "AND context.repertoire_id=%s)", (attempt_id, snapshot_id, repertoire_id),
    ).fetchone()


def insert_attempt(database, attempt_id, manifest_id, snapshot_id, repertoire_id, card_id,
                   revision, trained_color, context_json, manifest_json, started_at, study_timezone, queue_entry_id):
    database.execute_native(
        "INSERT INTO opening_evidence_attempts(attempt_id,manifest_id,presentation_snapshot_id,repertoire_id,card_id,"
        "card_revision,trained_color,context_json,manifest_json,started_at,study_timezone,queue_entry_id) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(attempt_id) DO NOTHING",
        (attempt_id, manifest_id, snapshot_id, repertoire_id, card_id, revision, trained_color,
         context_json, manifest_json, started_at, study_timezone, queue_entry_id),
    )


def attempt_for_update(database, attempt_id):
    return database.execute_native("SELECT * FROM opening_evidence_attempts WHERE attempt_id=%s FOR UPDATE", (attempt_id,)).fetchone()


def event(database, attempt_id, sequence):
    return database.execute_native(
        "SELECT event_json FROM opening_evidence_events WHERE attempt_id=%s AND sequence=%s", (attempt_id, sequence)
    ).fetchone()


def insert_event(database, attempt_id, sequence, event_json):
    database.execute_native("INSERT INTO opening_evidence_events(attempt_id,sequence,event_json) VALUES(%s,%s,%s)", (attempt_id, sequence, event_json))


def ordered_events(database, attempt_id):
    return database.execute_native(
        "SELECT sequence,event_json FROM opening_evidence_events WHERE attempt_id=%s ORDER BY sequence LIMIT 256", (attempt_id,)
    ).fetchall()


def update_checkpoint(database, contiguous_sequence, state, terminal_json, queue_entry_id, attempt_id):
    database.execute_native(
        "UPDATE opening_evidence_attempts SET contiguous_sequence=%s,state=%s,terminal_json=%s,"
        "queue_entry_id=COALESCE(queue_entry_id,%s) WHERE attempt_id=%s",
        (contiguous_sequence, state, terminal_json, queue_entry_id, attempt_id),
    )


def _read_checkpoint_source(
    database, attempt_id: str, *, lock: bool = False
) -> tuple[dict | None, tuple[tuple[int, str], ...]]:
    attempt = database.execute_native(
        "SELECT * FROM opening_evidence_attempts WHERE attempt_id=%s"
        + (" FOR UPDATE" if lock else ""),
        (attempt_id,),
    ).fetchone()
    events = database.execute_native(
        "SELECT sequence,event_json FROM opening_evidence_events WHERE attempt_id=%s ORDER BY sequence LIMIT 256",
        (attempt_id,),
    ).fetchall()
    return dict(attempt) if attempt else None, tuple((row[0], row[1]) for row in events)


def standalone_source(snapshot_id, queue_entry_id, repertoire_id, attempt_id):
    from tempo_learning_database.connections import read_connection as background_read_connection
    with background_read_connection() as database:
        snapshot = _presentation_snapshot(database, snapshot_id, queue_entry_id, repertoire_id)
        source_attempt, source_events = _read_checkpoint_source(database, attempt_id)
    return snapshot, source_attempt, source_events


def insert_attempt_returning(database, attempt_id, manifest_id, snapshot_id, repertoire_id, card_id,
                             revision, trained_color, context_json, manifest_json, started_at, study_timezone, queue_entry_id):
    return database.execute_native(
        "INSERT INTO opening_evidence_attempts(attempt_id,manifest_id,presentation_snapshot_id,repertoire_id,card_id,"
        "card_revision,trained_color,context_json,manifest_json,started_at,study_timezone,queue_entry_id) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(attempt_id) DO NOTHING RETURNING attempt_id",
        (attempt_id, manifest_id, snapshot_id, repertoire_id, card_id, revision, trained_color,
         context_json, manifest_json, started_at, study_timezone, queue_entry_id),
    ).fetchone()


def insert_missing_events(database, attempt_id, sequences, events_json):
    database.execute_native(
        "INSERT INTO opening_evidence_events(attempt_id,sequence,event_json) "
        "SELECT %s,source.sequence,source.event_json FROM UNNEST(%s::bigint[],%s::text[]) source(sequence,event_json)",
        (attempt_id, sequences, events_json),
    )


def complete_review(database, completed_at, review_id, result_json, attempt_id):
    database.execute_native(
        "UPDATE opening_evidence_attempts SET state='complete',completed_at=%s,review_id=%s,review_result_json=%s WHERE attempt_id=%s",
        (completed_at, review_id, result_json, attempt_id),
    )


def decision_summary(database, decision_id):
    return database.execute_native("SELECT * FROM opening_evidence_summaries WHERE decision_id=%s", (decision_id,)).fetchone()


def decision_observations(database, decision_id, count, offset):
    return database.execute_native(
        "SELECT observation.*,attempt.card_id,attempt.card_revision,attempt.manifest_id,attempt.repertoire_id,attempt.state "
        "FROM opening_evidence_observations observation JOIN opening_evidence_attempts attempt USING(attempt_id) "
        "WHERE decision_id=%s ORDER BY observed_at DESC,attempt_id DESC,decision_index DESC LIMIT %s OFFSET %s",
        (decision_id, count, offset),
    ).fetchall()


def recent_responses(database, decision_id):
    return database.execute_native(
        "SELECT attempt_id,decision_index,observation_json FROM opening_evidence_observations WHERE decision_id=%s "
        "AND observation_json::jsonb->>'response_at' IS NOT NULL "
        "ORDER BY observed_at DESC,attempt_id DESC,decision_index DESC LIMIT 20", (decision_id,),
    ).fetchall()


def latest_failure(database, decision_id):
    return database.execute_native(
        "SELECT attempt_id,failure_at FROM opening_evidence_observations WHERE decision_id=%s AND failure_at IS NOT NULL "
        "ORDER BY failure_at DESC,attempt_id DESC,decision_index DESC LIMIT 1", (decision_id,),
    ).fetchone()


def latest_observation(database, decision_id):
    return database.execute_native(
        "SELECT attempt_id,decision_index,observed_at FROM opening_evidence_observations WHERE decision_id=%s "
        "ORDER BY observed_at DESC,attempt_id DESC,decision_index DESC LIMIT 1", (decision_id,),
    ).fetchone()
