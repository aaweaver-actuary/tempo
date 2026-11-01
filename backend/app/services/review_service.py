"""One auditable scheduling transaction for study and gameplay evidence."""

from __future__ import annotations

from datetime import date, datetime
import json
from tempo_legacy_database.adapters.study.review import storage
from tempo_legacy_database.adapters.study.review.storage import sqlite3

from tempo_legacy_database.adapters.opening_progression import unlock_legacy_children_after_review
from .scheduler import schedule_review, unlock_ready
from app.study.queue_order import capture_daily_queue_order, preserve_daily_queue_order
from app.study.queue_placement import ensure_card_queued_after


def rebuild_defense_schedule(database: sqlite3.Connection, card_id: str,
                             light_first_interval_days: int) -> None:
    """Replay only valid reviews after a defective recognition rubric is retired."""
    rows = storage.valid_defense_reviews(database, card_id)
    if not rows:
        storage.reset_defense_schedule(database, card_id, lambda: date.today().isoformat())
        return
    interval_days = 0
    fsrs_card_json = None
    first_correct_at = None
    reinforcement_pending = False
    scheduling_mode = "normal"
    hard_correct_streak = 0
    recent_attempts: list[str] = []
    for row in rows:
        reviewed_at = datetime.fromisoformat(row["reviewed_at"])
        schedule = schedule_review(
            row["rating"], interval_days=interval_days,
            fsrs_card_json=fsrs_card_json, first_correct_at=first_correct_at,
            reinforcement_pending=reinforcement_pending,
            scheduling_mode=scheduling_mode, hard_correct_streak=hard_correct_streak,
            recent_attempts=recent_attempts,
            light_first_interval_days=light_first_interval_days,
            reviewed_at=reviewed_at, review_day=reviewed_at.date(),
        )
        interval_days = schedule.interval_days
        fsrs_card_json = schedule.fsrs_card_json
        first_correct_at = schedule.first_correct_at
        reinforcement_pending = schedule.reinforcement_pending
        scheduling_mode = schedule.scheduling_mode
        hard_correct_streak = schedule.hard_correct_streak
        recent_attempts = list(schedule.recent_attempts)
    successful_days = len({row["reviewed_at"][:10] for row in rows if row["rating"] == "correct"})
    recent_outcomes = [row["rating"] for row in reversed(rows[-2:])]
    state = "mature" if unlock_ready(schedule.stability, successful_days, recent_outcomes) else "learning"
    storage.save_defense_schedule(database, card_id, schedule, state)


def apply_scheduling_review(
    database: sqlite3.Connection,
    card_id: str,
    outcome: str,
    *,
    guided: bool,
    source_kind: str,
    source_ref: str | None,
    light_first_interval_days: int,
    reviewed_at: datetime,
    review_day: date,
) -> dict:
    from .scheduling_review_application import apply_scheduling_review as apply_review_to_store
    from tempo_legacy_database.adapters.scheduling_review_store import OwnedConnectionSchedulingReviewStore

    result = apply_review_to_store(
        OwnedConnectionSchedulingReviewStore(database), card_id, outcome,
        guided=guided, source_kind=source_kind, source_ref=source_ref,
        light_first_interval_days=light_first_interval_days,
        reviewed_at=reviewed_at, review_day=review_day,
    )
    if result["idempotent"]:
        return result
    if source_kind == "study":
        from .real_game_feedback_adapter import clear_satisfied_miss_priority
        clear_satisfied_miss_priority(database, card_id)
    unlock_legacy_children_after_review(database, card_id, review_day.isoformat(), result["state"])
    return result
