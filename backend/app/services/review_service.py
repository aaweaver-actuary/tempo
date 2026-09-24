"""One auditable scheduling transaction for study and gameplay evidence."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import sqlite3

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
    membership_hash = hashlib.sha256(
        ("queue-mix-v2\0" + "\0".join(
            f"{entry['id']}:{entry['card_id']}" for entry in entries
        )).encode()
    ).hexdigest()
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
    if source_ref:
        prior = database.execute(
            "SELECT 1 FROM reviews WHERE source_kind=? AND source_ref=?",
            (source_kind, source_ref),
        ).fetchone()
        if prior:
            card = database.execute(
                "SELECT due_date,interval_days,state,stability,scheduling_mode,hard_correct_streak FROM cards WHERE id=?",
                (card_id,),
            ).fetchone()
            return {
                "card_id": card_id, "next_due": card["due_date"], "interval_days": card["interval_days"],
                "state": card["state"], "requeue_today": True, "requeue_after_cards": 4,
                "stability": card["stability"], "scheduling_mode": card["scheduling_mode"],
                "hard_correct_streak": card["hard_correct_streak"], "suggest_shorter_prefix": False,
                "idempotent": True,
            }
    card = database.execute(
        """SELECT interval_days,fsrs_card_json,first_correct_at,reinforcement_pending,
                  scheduling_mode,hard_correct_streak,recent_attempts_json
           FROM cards WHERE id=? AND archived=0""",
        (card_id,),
    ).fetchone()
    if not card:
        raise KeyError("Card not found")
    schedule = schedule_review(
        outcome,
        interval_days=card["interval_days"],
        fsrs_card_json=card["fsrs_card_json"],
        first_correct_at=card["first_correct_at"],
        reinforcement_pending=bool(card["reinforcement_pending"]),
        scheduling_mode=card["scheduling_mode"],
        hard_correct_streak=card["hard_correct_streak"],
        recent_attempts=json.loads(card["recent_attempts_json"] or "[]"),
        light_first_interval_days=light_first_interval_days,
        reviewed_at=reviewed_at,
        review_day=review_day,
    )
    database.execute(
        """INSERT INTO reviews(card_id,rating,internal_rating,guided,reviewed_at,previous_interval,next_interval,source_kind,source_ref)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            card_id, outcome, schedule.internal_rating, int(guided), reviewed_at.isoformat(),
            card["interval_days"], schedule.interval_days, source_kind, source_ref,
        ),
    )
    successful_days = database.execute(
        "SELECT COUNT(DISTINCT date(reviewed_at)) FROM reviews WHERE card_id=? AND rating='correct' AND invalidated_at IS NULL",
        (card_id,),
    ).fetchone()[0]
    seed = database.execute(
        """SELECT baseline_successful_days,baseline_recent_clean
           FROM opening_card_schedule_seeds WHERE card_id=?""",
        (card_id,),
    ).fetchone()
    successful_days += int(seed["baseline_successful_days"] if seed else 0)
    recent_outcomes = [row[0] for row in database.execute(
        "SELECT rating FROM reviews WHERE card_id=? AND invalidated_at IS NULL ORDER BY reviewed_at DESC,id DESC LIMIT 2", (card_id,)
    )]
    if seed:
        recent_outcomes.extend(
            ["correct"]
            * min(int(seed["baseline_recent_clean"]), max(0, 2 - len(recent_outcomes)))
        )
    state = "mature" if unlock_ready(schedule.stability, successful_days, recent_outcomes) else "learning"
    database.execute(
        """UPDATE cards SET due_date=?,interval_days=?,fsrs_card_json=?,first_correct_at=?,reinforcement_pending=?,
           stability=?,guided_review=?,state=?,scheduling_mode=?,hard_correct_streak=?,recent_attempts_json=? WHERE id=?""",
        (
            schedule.due_date.isoformat(), schedule.interval_days, schedule.fsrs_card_json,
            schedule.first_correct_at, int(schedule.reinforcement_pending), schedule.stability,
            int(guided), state, schedule.scheduling_mode, schedule.hard_correct_streak,
            json.dumps(schedule.recent_attempts), card_id,
        ),
    )
    if state == "mature":
        database.execute(
            "UPDATE cards SET state='new',due_date=? WHERE unlock_after_card_id=? AND state='locked'",
            (review_day.isoformat(), card_id),
        )
    return {
        "card_id": card_id,
        "next_due": schedule.due_date.isoformat(),
        "interval_days": schedule.interval_days,
        "state": state,
        "requeue_today": schedule.requeue_today,
        "requeue_after_cards": schedule.requeue_after_cards,
        "stability": schedule.stability,
        "scheduling_mode": schedule.scheduling_mode,
        "hard_correct_streak": schedule.hard_correct_streak,
        "suggest_shorter_prefix": schedule.suggest_shorter_prefix,
        "idempotent": False,
    }


def ensure_card_queued_after(
    database: sqlite3.Connection,
    card_id: str,
    after_cards: int = 4,
    attempt_state: str = "gameplay",
    priority_reason: str | None = None,
) -> None:
    day = date.today().isoformat()
    entry = database.execute(
        "SELECT id FROM daily_queue WHERE queue_date=? AND card_id=? AND status='queued' ORDER BY position,id LIMIT 1",
        (day, card_id),
    ).fetchone()
    if not entry:
        cycle = database.execute(
            "SELECT COALESCE(MAX(cycle),-1)+1 FROM daily_queue WHERE queue_date=? AND card_id=?",
            (day, card_id),
        ).fetchone()[0]
        database.execute(
            "INSERT INTO daily_queue(queue_date,card_id,cycle,position,attempt_state,gameplay_priority_reason) VALUES(?,?,?,?,?,?)",
            (day, card_id, cycle, 2_000_000_000, attempt_state, priority_reason),
        )
        entry = database.execute("SELECT last_insert_rowid() AS id").fetchone()
    elif priority_reason:
        database.execute(
            "UPDATE daily_queue SET gameplay_priority_reason=? WHERE id=?",
            (priority_reason, entry["id"]),
        )
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
               admission_kind='review'
           WHERE id=?""",
        (entry["id"],),
    )
    preserve_daily_queue_order(database, day)
