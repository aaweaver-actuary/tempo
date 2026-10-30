"""One auditable scheduling transaction for study and gameplay evidence."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from tempo_legacy_database.adapters.study.review import storage
from tempo_legacy_database.adapters.study.review.storage import sqlite3

from tempo_legacy_database.adapters.opening_progression import unlock_legacy_children_after_review
from .scheduler import schedule_review, unlock_ready
from .queue_presentation import presentation_rows
from .queue_ordering import _priority_queue_order
from .queue_priority_promotion import StalePriorityPromotion, priority_changes


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


def _queue_order_inputs(database, queue_date):
    entries = storage.queued_order_entries(database, queue_date)
    from .frozen_opening_order import activation
    saved = storage.queue_membership_receipt(database, queue_date)
    return entries, saved, activation(database)


def capture_daily_queue_order(database, queue_date):
    """Prove the pre-mutation rows/token have actually received presentation."""
    from .queue_ordering import _queue_order_provenance
    return _queue_order_provenance(*_queue_order_inputs(database, queue_date))


def preserve_daily_queue_order(database: sqlite3.Connection, queue_date: str, *, previous=None) -> None:
    """Carry a verified receipt across one controlled positional mutation."""
    from .queue_ordering import _preserved_queue_fingerprint
    membership_hash = _preserved_queue_fingerprint(*_queue_order_inputs(database, queue_date), previous=previous)
    seed = int(hashlib.sha256(queue_date.encode()).hexdigest()[:15], 16)
    storage.write_queue_order_receipt(database, queue_date, seed, membership_hash, lambda: datetime.now(timezone.utc).isoformat())


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


def ensure_card_queued_after(
    database: sqlite3.Connection,
    card_id: str,
    after_cards: int = 4,
    attempt_state: str = "gameplay",
    priority_reason: str | None = None,
    *, queue_date: str | None = None, protect_presented: bool = False,
    preserve_order: bool = True, prepared_priority=None, priority_order=None,
) -> None:
    day = queue_date or date.today().isoformat()
    previous_order = capture_daily_queue_order(database, day) if preserve_order else None
    if prepared_priority is not None:
        if (not protect_presented or preserve_order
                or (day,card_id,priority_reason) != (prepared_priority.queue_date,
                    prepared_priority.card_id,prepared_priority.priority_reason)
                or storage.priority_ownership_state(database,day,target_card_id=card_id) != prepared_priority.ownership_state):
            raise StalePriorityPromotion('priority_presentation_changed')
    entry = storage.queued_entry(database, day, card_id)
    if prepared_priority is not None and (not entry or entry['id'] != prepared_priority.entry_id):
        raise StalePriorityPromotion('priority_target_changed')
    if not entry:
        cycle = storage.next_cycle(database, day, card_id)
        entry = storage.insert_entry(database, day, card_id, cycle, protect_presented, attempt_state, priority_reason)
    elif priority_reason:
        storage.set_priority(database, priority_reason, entry["id"])
    if protect_presented:
        if prepared_priority is not None:
            changes = prepared_priority.changes
        else:
            rows = presentation_rows(database, day)
            if priority_reason in {'product_game_lapse','product_game_encounter'}:
                changes = priority_changes(rows,card_id,priority_reason,priority_order=priority_order)
            else:
                changes = tuple((row['id'],row['position'],slot['position'])
                    for slot,row in zip(sorted(rows,key=lambda row:(row['position'],row['id'])),
                                        _priority_queue_order(rows))
                    if slot['position'] != row['position'])
        try:
            storage.apply_priority_positions(database,day,changes)
        except storage.PriorityPositionConflict as error:
            raise StalePriorityPromotion('priority_positions_changed') from error
    else:
        ordered_ids = [row[0] for row in storage.other_queued_entries(database, day, entry["id"])]
        ordered_ids.insert(min(after_cards, len(ordered_ids)), entry["id"])
        for position, entry_id in enumerate(ordered_ids):
            storage.set_position(database, position, entry_id)
    storage.classify_entry(database, entry["id"])
    if preserve_order:
        preserve_daily_queue_order(database, day, previous=previous_order)
