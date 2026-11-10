"""Independent-review facts, schedule snapshots and bound persistence."""
from __future__ import annotations
import json


SCHEDULE_COLUMNS = (
    "due_date", "interval_days", "fsrs_card_json", "first_correct_at",
    "reinforcement_pending", "stability", "guided_review", "state",
    "scheduling_mode", "hard_correct_streak", "recent_attempts_json",
)


def card_schedule_state(database, card_id: str) -> dict | None:
    row = database.execute(
        f"SELECT {','.join(SCHEDULE_COLUMNS)} FROM cards WHERE id=?",
        (card_id,),
    ).fetchone()
    return dict(row) if row else None


def save_schedule_snapshot(database, review_id: int, state: dict) -> None:
    database.execute(
        "INSERT INTO review_schedule_snapshots(review_id,state_json) VALUES(?,?) "
        "ON CONFLICT(review_id) DO UPDATE SET state_json=excluded.state_json",
        (review_id, json.dumps(state)),
    )


def write_card_state(database, card_id: str, state: dict) -> None:
    database.execute(
        f"UPDATE cards SET {','.join(f'{column}=?' for column in SCHEDULE_COLUMNS)} WHERE id=?",
        (*[state[column] for column in SCHEDULE_COLUMNS], card_id),
    )


def light_interval_setting(database):
    return database.execute(
        "SELECT light_first_interval_days FROM settings WHERE id=1"
    ).fetchone()


def valid_review_history(database, card_id):
    rows = database.execute(
        "SELECT id,rating,reviewed_at FROM reviews WHERE card_id=? AND invalidated_at IS NULL",
        (card_id,),
    )
    yield from rows


def baseline_schedule_seed(database, card_id):
    return database.execute(
        "SELECT baseline_successful_days,baseline_recent_clean FROM opening_card_schedule_seeds WHERE card_id=?",
        (card_id,),
    ).fetchone()


def review_attempt_receipt(database, attempt_id):
    return database.execute(
        "SELECT card_id,queue_entry_id,outcome,guided,completed_at,result_json "
        "FROM review_attempt_receipts WHERE attempt_id=?",
        (attempt_id,),
    ).fetchone()


def card_revision(database, card_id):
    return database.execute(
        "SELECT revision FROM cards WHERE id=?", (card_id,)
    ).fetchone()


def ordered_valid_reviews(database, card_id):
    rows = database.execute(
        "SELECT id,rating,guided,reviewed_at FROM reviews "
        "WHERE card_id=? AND invalidated_at IS NULL ORDER BY reviewed_at,id",
        (card_id,),
    )
    yield from rows


def review_snapshot(database, review_id):
    return database.execute(
        "SELECT state_json FROM review_schedule_snapshots WHERE review_id=?",
        (review_id,),
    ).fetchone()


def insert_independent_review(database, *, card_id, rating, internal_rating, guided,
                              reviewed_at, previous_interval, next_interval, source_kind, source_ref):
    database.execute(
        "INSERT INTO reviews(card_id,rating,internal_rating,guided,reviewed_at,previous_interval,next_interval,source_kind,source_ref) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (card_id, rating, internal_rating, guided, reviewed_at, previous_interval,
         next_interval, source_kind, source_ref),
    )


def independent_review_id(database, source_ref):
    return database.execute(
        "SELECT id FROM reviews WHERE source_kind='study' AND source_ref=?",
        (source_ref,),
    ).fetchone()[0]


def update_review_intervals(database, previous_interval, next_interval, review_id):
    database.execute(
        "UPDATE reviews SET previous_interval=?,next_interval=? WHERE id=?",
        (previous_interval, next_interval, review_id),
    )


def set_conservative_due(database, due_date, card_id):
    database.execute(
        "UPDATE cards SET due_date=? WHERE id=?",
        (due_date, card_id),
    )


def retain_attempt_receipt(database, *, attempt_id, card_id, queue_entry_id, outcome, guided,
                            completed_at, review_id, scheduling_status, warning, result_json):
    database.execute(
        "INSERT INTO review_attempt_receipts(attempt_id,card_id,queue_entry_id,outcome,guided,completed_at,review_id,scheduling_status,warning,result_json) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (attempt_id, card_id, queue_entry_id, outcome, guided, completed_at,
         review_id, scheduling_status, warning, result_json),
    )
