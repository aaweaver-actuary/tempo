"""One auditable scheduling transaction for study and gameplay evidence."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import sqlite3

from .opening_progression import unlock_legacy_children_after_review
from .scheduler import schedule_review, unlock_ready


def rebuild_defense_schedule(database: sqlite3.Connection, card_id: str,
                             light_first_interval_days: int) -> None:
    """Replay only valid reviews after a defective recognition rubric is retired."""
    rows = database.execute(
        """SELECT rating,reviewed_at FROM reviews WHERE card_id=? AND invalidated_at IS NULL
           ORDER BY reviewed_at,id""", (card_id,),
    ).fetchall()
    if not rows:
        database.execute(
            """UPDATE cards SET due_date=?,interval_days=0,fsrs_card_json=NULL,
               first_correct_at=NULL,reinforcement_pending=0,stability=0,
               guided_review=0,state='learning',scheduling_mode='normal',
               hard_correct_streak=0,recent_attempts_json='[]' WHERE id=? AND content_type='defense'""",
            (date.today().isoformat(), card_id),
        )
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
    database.execute(
        """UPDATE cards SET due_date=?,interval_days=?,fsrs_card_json=?,first_correct_at=?,
           reinforcement_pending=?,stability=?,guided_review=0,state=?,scheduling_mode=?,
           hard_correct_streak=?,recent_attempts_json=? WHERE id=? AND content_type='defense'""",
        (schedule.due_date.isoformat(), schedule.interval_days, schedule.fsrs_card_json,
         schedule.first_correct_at, int(schedule.reinforcement_pending), schedule.stability,
         state, schedule.scheduling_mode, schedule.hard_correct_streak,
         json.dumps(schedule.recent_attempts), card_id),
    )


def preserve_daily_queue_order(database: sqlite3.Connection, queue_date: str) -> None:
    """Keep a manually positioned queue entry stable across projection rebuilds."""
    entries = database.execute(
        """SELECT id,card_id FROM daily_queue
           WHERE queue_date=? AND status='queued' ORDER BY id""",
        (queue_date,),
    ).fetchall()
    from .queue_ordering import _preserved_queue_fingerprint
    from .frozen_opening_order import activation
    saved = database.execute("SELECT membership_hash FROM daily_queue_days WHERE queue_date=?", (queue_date,)).fetchone()
    membership_hash = _preserved_queue_fingerprint(entries, saved, activation(database))
    seed = int(hashlib.sha256(queue_date.encode()).hexdigest()[:15], 16)
    database.execute(
        """INSERT INTO daily_queue_days(queue_date,seed,membership_hash,generated_at)
           VALUES(?,?,?,?) ON CONFLICT(queue_date) DO UPDATE SET
           membership_hash=excluded.membership_hash,generated_at=excluded.generated_at""",
        (queue_date, seed, membership_hash, datetime.now(timezone.utc).isoformat()),
    )


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
    from .scheduling_review_store import OwnedConnectionSchedulingReviewStore

    result = apply_review_to_store(
        OwnedConnectionSchedulingReviewStore(database), card_id, outcome,
        guided=guided, source_kind=source_kind, source_ref=source_ref,
        light_first_interval_days=light_first_interval_days,
        reviewed_at=reviewed_at, review_day=review_day,
    )
    if result["idempotent"]:
        return result
    if source_kind == "study":
        from .real_game_feedback import clear_satisfied_miss_priority
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
    preserve_order: bool = True,
) -> None:
    day = queue_date or date.today().isoformat()
    entry = database.execute(
        "SELECT id FROM daily_queue WHERE queue_date=? AND card_id=? AND status='queued' ORDER BY position,id LIMIT 1",
        (day, card_id),
    ).fetchone()
    if not entry:
        cycle = database.execute(
            "SELECT COALESCE(MAX(cycle),-1)+1 FROM daily_queue WHERE queue_date=? AND card_id=?",
            (day, card_id),
        ).fetchone()[0]
        entry = database.execute(
            "INSERT INTO daily_queue(queue_date,card_id,cycle,position,attempt_state,gameplay_priority_reason) VALUES(?,?,?,?,?,?) RETURNING id",
            (day, card_id, cycle,
             database.execute("SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?", (day,)).fetchone()[0]
             if protect_presented else 2_000_000_000, attempt_state, priority_reason),
        ).fetchone()
    elif priority_reason:
        database.execute(
            "UPDATE daily_queue SET gameplay_priority_reason=? WHERE id=?",
            (priority_reason, entry["id"]),
        )
    if protect_presented:
        from .queue_application import presentation_rows
        from .queue_ordering import _priority_queue_order
        rows = presentation_rows(database, day)
        for slot, row in zip(sorted(rows, key=lambda row: (row['position'], row['id'])), _priority_queue_order(rows)):
            if slot['position'] != row['position']:
                database.execute("UPDATE daily_queue SET position=? WHERE id=?", (slot['position'], row['id']))
    else:
        ordered_ids = [row[0] for row in database.execute(
            "SELECT id FROM daily_queue WHERE queue_date=? AND status='queued' AND id!=? ORDER BY position,id",
            (day, entry["id"]),
        )]
        ordered_ids.insert(min(after_cards, len(ordered_ids)), entry["id"])
        for position, entry_id in enumerate(ordered_ids):
            database.execute("UPDATE daily_queue SET position=? WHERE id=?", (position, entry_id))
    database.execute(
        """UPDATE daily_queue SET
               card_bucket=(SELECT content_type FROM cards WHERE id=card_id),
               admission_kind=CASE WHEN admission_kind='explicit' THEN 'explicit'
                   WHEN EXISTS(SELECT 1 FROM reviews review
                   WHERE review.card_id=daily_queue.card_id AND review.source_kind='study'
                     AND review.invalidated_at IS NULL) THEN 'review' ELSE 'new' END
           WHERE id=?""",
        (entry["id"],),
    )
    if preserve_order:
        preserve_daily_queue_order(database, day)
