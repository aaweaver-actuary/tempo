from __future__ import annotations
from datetime import datetime, timezone
from .clock import date
import hashlib, json
from tempo_learning_database.adapters.postgres_store import PostgresConnection
from .scheduler import schedule_review, unlock_ready
from tempo_learning_database.adapters.opening_progression import unlock_legacy_children_after_review
from tempo_learning_database.adapters import review_service as storage


def rebuild_defense_schedule(
    database: PostgresConnection, card_id: str, light_first_interval_days: int
) -> None:
    """Replay only valid reviews after a defective recognition rubric is retired."""
    rows = storage.valid_defense_reviews(database, card_id)
    if not rows:
        storage.reset_defense_schedule(database, date.today().isoformat(), card_id)
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
            row["rating"],
            interval_days=interval_days,
            fsrs_card_json=fsrs_card_json,
            first_correct_at=first_correct_at,
            reinforcement_pending=reinforcement_pending,
            scheduling_mode=scheduling_mode,
            hard_correct_streak=hard_correct_streak,
            recent_attempts=recent_attempts,
            light_first_interval_days=light_first_interval_days,
            reviewed_at=reviewed_at,
            review_day=reviewed_at.date(),
        )
        interval_days = schedule.interval_days
        fsrs_card_json = schedule.fsrs_card_json
        first_correct_at = schedule.first_correct_at
        reinforcement_pending = schedule.reinforcement_pending
        scheduling_mode = schedule.scheduling_mode
        hard_correct_streak = schedule.hard_correct_streak
        recent_attempts = list(schedule.recent_attempts)
    successful_days = len(
        {row["reviewed_at"][:10] for row in rows if row["rating"] == "correct"}
    )
    recent_outcomes = [row["rating"] for row in reversed(rows[-2:])]
    state = (
        "mature"
        if unlock_ready(schedule.stability, successful_days, recent_outcomes)
        else "learning"
    )
    storage.write_defense_schedule(
        database, due_date=schedule.due_date.isoformat(), interval_days=schedule.interval_days,
        fsrs_card_json=schedule.fsrs_card_json, first_correct_at=schedule.first_correct_at,
        reinforcement_pending=int(schedule.reinforcement_pending), stability=schedule.stability,
        state=state, scheduling_mode=schedule.scheduling_mode,
        hard_correct_streak=schedule.hard_correct_streak,
        recent_attempts_json=json.dumps(schedule.recent_attempts), card_id=card_id,
    )


def _queue_order_inputs(database, queue_date):
    entries = storage.queue_order_entries(database, queue_date)
    from .frozen_opening_order import activation
    saved = storage.saved_order_membership(database, queue_date)
    return entries, saved, activation(database)


def capture_daily_queue_order(database, queue_date):
    """Prove the pre-mutation rows/token have actually received presentation."""
    from .queue_ordering import _queue_order_provenance
    return _queue_order_provenance(*_queue_order_inputs(database, queue_date))


def preserve_daily_queue_order(database: PostgresConnection, queue_date: str, *, previous=None) -> None:
    """Carry a verified receipt across one controlled positional mutation."""
    from .queue_ordering import _preserved_queue_fingerprint
    membership_hash = _preserved_queue_fingerprint(*_queue_order_inputs(database, queue_date), previous=previous)
    seed = int(hashlib.sha256(queue_date.encode()).hexdigest()[:15], 16)
    storage.retain_daily_order(database, queue_date, seed, membership_hash, datetime.now(timezone.utc).isoformat())


def apply_scheduling_review(
    database: PostgresConnection,
    card_id: str,
    outcome: str,
    *,
    guided: bool,
    source_kind: str,
    source_ref: str | None,
    light_first_interval_days: int,
    reviewed_at: datetime,
    review_day: date,
    queue_entry_id: int | None = None,
    expected_revision: int | None = None,
    request_followup=None,
) -> dict:
    from .scheduling_review_application import (
        apply_scheduling_review as apply_review_to_store,
    )
    from tempo_learning_database.adapters.scheduling_review_store import OwnedConnectionSchedulingReviewStore

    result = apply_review_to_store(
        OwnedConnectionSchedulingReviewStore(database),
        card_id,
        outcome,
        guided=guided,
        source_kind=source_kind,
        source_ref=source_ref,
        light_first_interval_days=light_first_interval_days,
        reviewed_at=reviewed_at,
        review_day=review_day,
    )
    if result["idempotent"]:
        return result
    from .receivers import settle

    review = storage.latest_review_id(database, card_id)
    if source_kind == "study":
        settle(
            database,
            card_id,
            review,
            queue_entry_id=queue_entry_id,
            revision=expected_revision,
            request_followup=request_followup,
        )
    unlock_legacy_children_after_review(
        database, card_id, review_day.isoformat(), result["state"]
    )
    return result


def ensure_card_queued_after(
    database: PostgresConnection,
    card_id: str,
    after_cards: int = 4,
    attempt_state: str = "gameplay",
    priority_reason: str | None = None,
    *,
    queue_date: str | None = None,
    protected_ids: set[int] | None = None,
    preserve_order: bool = True,
) -> None:
    day = queue_date or date.today().isoformat()
    previous_order = capture_daily_queue_order(database, day) if preserve_order else None
    entry = storage.queued_entry(database, day, card_id)
    if not entry:
        cycle = storage.next_cycle(database, day, card_id)
        append_position = (
            storage.append_position(database, day)
            if protected_ids is not None
            else 2_000_000_000
        )
        entry = storage.insert_queued_entry(database, day, card_id, cycle, append_position, attempt_state, priority_reason)
    elif priority_reason:
        storage.retain_priority_reason(database, priority_reason, entry["id"])
    if protected_ids is not None:
        from tempo_learning_remediation import (
            Candidate,
            order_candidates,
        )
        from tempo_learning_database.connections import current_database

        rows = storage.priority_queue_rows(database, current_database.get().configuration.workspace_id, day)
        if entry["id"] not in protected_ids:
            misses = [
                row
                for row in rows
                if row["id"] not in protected_ids
                and row["gameplay_priority_reason"] == priority_reason
                and row["current_miss"]
            ]
            preferences = order_candidates(
                tuple(
                    Candidate(
                        row["card_id"],
                        row["revision"],
                        row["publication_id"] or "",
                        row["publication_generation"],
                        row["admission_repertoire_id"] or "",
                        True,
                        True,
                    )
                    for row in misses
                )
            )
            priority_ids = [
                row["id"]
                for preference in preferences
                for row in misses
                if row["card_id"] == preference.card_id
            ]
            priority_set = set(priority_ids)
            movable = iter(
                [
                    *priority_ids,
                    *[
                        row["id"]
                        for row in rows
                        if row["id"] not in protected_ids
                        and row["id"] not in priority_set
                    ],
                ]
            )
            updates = []
            for row in rows:
                target = row["id"] if row["id"] in protected_ids else next(movable)
                updates.append((row["position"], target))
            storage.update_queue_positions(database, updates)
    else:
        _position_requeued_card(database, day, entry["id"], after_cards)
    storage.refresh_entry_metadata(database, entry["id"])
    if preserve_order:
        preserve_daily_queue_order(database, day, previous=previous_order)


def _position_requeued_card(database, day, entry_id, after_cards):
    ordered_ids = storage.other_queued_ids(database, day, entry_id)
    ordered_ids.insert(min(after_cards, len(ordered_ids)), entry_id)
    for position, entry_id in enumerate(ordered_ids):
        storage.set_queue_position(database, position, entry_id)
