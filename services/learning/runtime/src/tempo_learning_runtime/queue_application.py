from __future__ import annotations
from tempo_learning_database.adapters.postgres_store import PostgresConnection
from .durable_tasks import enqueue_task_in_transaction
from .review_service import preserve_daily_queue_order
from dataclasses import dataclass
import json
from typing import Any
from tempo_learning_database.adapters import queue_application as storage
from .real_game_feedback import MISS_REASON


def presentation_rows(database, day):
    """Runtime adapter supplies the same explicit facts to the pure mixer."""
    rows, reviewed, eligible = storage.presentation_facts(database, day, MISS_REASON)
    if not rows:
        return rows
    for row in rows:
        row['admission_kind'] = ('explicit' if row['admission_kind']=='explicit'
                                 else 'review' if row.get('cycle',0)>0 or row['card_id'] in reviewed else 'new')
        row['eligible'] = row['id'] in eligible
    return rows


def requeue(db, day, cid, after, attempt):
    from .review_service import capture_daily_queue_order
    previous_order = capture_daily_queue_order(db, day)
    cycle = storage.next_cycle(db, day, cid)
    if after is None:
        position = storage.next_position(db, day)
    else:
        row = storage.available_position_at_offset(db, day, max(0, after), MISS_REASON)
        position = (
            row[0]
            if row
            else storage.next_position(db, day)
        )
        storage.shift_queued_positions(db, day, position)
    inserted_entry_id = storage.insert_repeated_entry(db, day, cid, cycle, position, attempt)
    preserve_daily_queue_order(db, day, previous=previous_order)
    return inserted_entry_id


def request_queue_refresh_in_transaction(
    database: PostgresConnection,
    queue_date: str,
    *,
    preserve_through_entry_id: int | None = None,
) -> dict[str, Any]:
    """Checkpoint a foreground mutation and its follow-up queue refresh together."""

    payload = {"queue_date": queue_date}
    if preserve_through_entry_id is not None:
        payload["preserve_through_entry_id"] = preserve_through_entry_id
    task = enqueue_task_in_transaction(
        database,
        "daily_queue",
        "current",
        payload,
        priority=10,
    )
    from .remediation_adapter import request_remediation
    request_remediation(database, queue_date)
    mark_queue_refresh_pending_in_transaction(database, queue_date)
    return task


def available_attempt_sql() -> str:
    return storage.available_attempt_sql(MISS_REASON)


def _available_attempt_where_sql() -> str:
    return storage._available_attempt_where_sql(MISS_REASON)


mark_queue_refresh_pending_in_transaction = storage.mark_queue_refresh_pending_in_transaction


@dataclass(frozen=True)
class QueueWindowProjection:
    """Learning-owned legacy card projection before authoritative head issuance."""

    cards: list[dict[str, Any]]
    available_count: int
    projection: dict[str, Any] | None


def read_queue_window(
    db: PostgresConnection, queue_date: str, *, limit: int,
) -> QueueWindowProjection:
    """Read complete queue facts in the caller's read-only transaction."""
    # Today/prepared retain their 200-card legacy window; HTTP /window caps at 20.
    if not 1 <= limit <= 200:
        raise ValueError("Queue projection limit must be between 1 and 200")
    rows = storage.window_rows(db, queue_date, limit, MISS_REASON)
    cards = []
    for row in rows:
        card = dict(row)
        card["moves"] = json.loads(card.pop("moves_json"))
        card["latest_review_id"] = storage.latest_review_id(db, card["id"])
        if card["content_type"] == "defense":
            accepted_defense = storage.accepted_defense(db, card["id"], card["revision"])
            card["source_ref"] = accepted_defense[0] if accepted_defense else None
        if card["content_type"] == "study_exercise":
            spec = storage.study_specification(db, card["study_exercise_id"], card["revision"])
            card["study_snapshot"] = {
                "schema_version": 1,
                "grader_version": 1,
                "exercise_id": card["study_exercise_id"],
                "revision": card["revision"],
                "fen": card["start_fen"],
                "specification": json.loads(spec[0]),
            }
        # Explicit legacy protocol boundary: internal acceptance metadata is not wire data.
        allowed = "id queue_entry_id start_fen moves content_type repertoire_name repertoire_source cycle position attempt_state attempt_failed gameplay_priority_reason admission_kind admission_source first_correct_at recent_attempts_json source_ref is_main trained_color revision repertoire_id study_id study_exercise_id study_snapshot kind state due_date interval_days ease repetitions lapses unlock_after_card_id fsrs_card_json reinforcement_pending stability guided_review maximum_interval scheduling_mode hard_correct_streak archived superseded_by source_fen introduced_at pending_validation canonical_route_source latest_review_id".split()
        cards.append({key: card[key] for key in allowed if key in card})
    projection = storage.projection(db, queue_date)
    count = storage.available_count(db, queue_date, MISS_REASON)
    return QueueWindowProjection(cards, count, dict(projection) if projection else None)
