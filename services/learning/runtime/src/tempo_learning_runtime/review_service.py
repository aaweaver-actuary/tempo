from __future__ import annotations
from datetime import datetime, timezone
from .clock import date
import hashlib, json
from .postgres_store import PostgresConnection
from .scheduler import schedule_review, unlock_ready
from .opening_progression import unlock_legacy_children_after_review


def rebuild_defense_schedule(
    database: PostgresConnection, card_id: str, light_first_interval_days: int
) -> None:
    """Replay only valid reviews after a defective recognition rubric is retired."""
    rows = database.execute(
        """SELECT rating,reviewed_at FROM reviews WHERE card_id=? AND invalidated_at IS NULL
           ORDER BY reviewed_at,id""",
        (card_id,),
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
    database.execute(
        """UPDATE cards SET due_date=?,interval_days=?,fsrs_card_json=?,first_correct_at=?,
           reinforcement_pending=?,stability=?,guided_review=0,state=?,scheduling_mode=?,
           hard_correct_streak=?,recent_attempts_json=? WHERE id=? AND content_type='defense'""",
        (
            schedule.due_date.isoformat(),
            schedule.interval_days,
            schedule.fsrs_card_json,
            schedule.first_correct_at,
            int(schedule.reinforcement_pending),
            schedule.stability,
            state,
            schedule.scheduling_mode,
            schedule.hard_correct_streak,
            json.dumps(schedule.recent_attempts),
            card_id,
        ),
    )


def preserve_daily_queue_order(database: PostgresConnection, queue_date: str) -> None:
    """Keep a manually positioned queue entry stable across projection rebuilds."""
    entries = database.execute(
        """SELECT id,card_id FROM daily_queue
           WHERE queue_date=? AND status='queued' ORDER BY id""",
        (queue_date,),
    ).fetchall()
    membership_hash = hashlib.sha256(
        (
            "queue-mix-v2\0"
            + "\0".join(f"{entry['id']}:{entry['card_id']}" for entry in entries)
        ).encode()
    ).hexdigest()
    seed = int(hashlib.sha256(queue_date.encode()).hexdigest()[:15], 16)
    database.execute(
        """INSERT INTO daily_queue_days(queue_date,seed,membership_hash,generated_at)
           VALUES(?,?,?,?) ON CONFLICT(queue_date) DO UPDATE SET
           membership_hash=excluded.membership_hash,generated_at=excluded.generated_at""",
        (queue_date, seed, membership_hash, datetime.now(timezone.utc).isoformat()),
    )


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
) -> dict:
    from .scheduling_review_application import (
        apply_scheduling_review as apply_review_to_store,
    )
    from .scheduling_review_store import OwnedConnectionSchedulingReviewStore

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

    review = database.execute(
        "SELECT MAX(id) FROM reviews WHERE card_id=?", (card_id,)
    ).fetchone()[0]
    if source_kind == "study":
        settle(
            database,
            card_id,
            review,
            queue_entry_id=queue_entry_id,
            revision=expected_revision,
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
) -> None:
    day = queue_date or date.today().isoformat()
    entry = database.execute(
        "SELECT id FROM daily_queue WHERE queue_date=? AND card_id=? AND status='queued' ORDER BY position,id LIMIT 1",
        (day, card_id),
    ).fetchone()
    if not entry:
        cycle = database.execute(
            "SELECT COALESCE(MAX(cycle),-1)+1 FROM (SELECT cycle FROM daily_queue WHERE queue_date=? AND card_id=? UNION ALL SELECT cycle FROM queue_attempt_origins WHERE queue_date=? AND card_id=?) cycles",
            (day, card_id, day, card_id),
        ).fetchone()[0]
        append_position = (
            database.execute(
                "SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?",
                (day,),
            ).fetchone()[0]
            if protected_ids is not None
            else 2_000_000_000
        )
        entry = database.execute(
            "INSERT INTO daily_queue(queue_date,card_id,cycle,position,attempt_state,gameplay_priority_reason) VALUES(?,?,?,?,?,?) RETURNING id",
            (day, card_id, cycle, append_position, attempt_state, priority_reason),
        ).fetchone()
    elif priority_reason:
        database.execute(
            "UPDATE daily_queue SET gameplay_priority_reason=? WHERE id=?",
            (priority_reason, entry["id"]),
        )
    if protected_ids is not None:
        from tempo_learning_remediation import (
            Candidate,
            current_miss_sql,
            order_candidates,
        )
        from .database import current_database

        rows = database.execute_native(
            """SELECT q.id,q.position,q.card_id,q.gameplay_priority_reason,q.admission_repertoire_id,
                      card.revision,card.publication_id,card.publication_generation,
                      CASE WHEN q.gameplay_priority_reason IS NOT NULL THEN """
            + current_miss_sql("card")
            + """
                      ELSE FALSE END AS current_miss
               FROM daily_queue q JOIN cards card ON card.id=q.card_id
               WHERE q.queue_date=%s AND q.status='queued' ORDER BY q.position,q.id""",
            (current_database.get().configuration.workspace_id, day),
        ).fetchall()
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
            database.executemany(
                "UPDATE daily_queue SET position=? WHERE id=?", updates
            )
    else:
        _position_requeued_card(database, day, entry["id"], after_cards)
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
    preserve_daily_queue_order(database, day)


def _position_requeued_card(database, day, entry_id, after_cards):
    ordered_ids = [
        row[0]
        for row in database.execute(
            "SELECT id FROM daily_queue WHERE queue_date=? AND status='queued' AND id!=? ORDER BY position,id",
            (day, entry_id),
        )
    ]
    ordered_ids.insert(min(after_cards, len(ordered_ids)), entry_id)
    for position, entry_id in enumerate(ordered_ids):
        database.execute(
            "UPDATE daily_queue SET position=? WHERE id=?", (position, entry_id)
        )
