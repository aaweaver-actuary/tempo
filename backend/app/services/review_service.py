"""One auditable scheduling transaction for study and gameplay evidence."""

from __future__ import annotations

from datetime import date, datetime
import json
import sqlite3

from .scheduler import schedule_review, unlock_ready


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
        "SELECT COUNT(DISTINCT date(reviewed_at)) FROM reviews WHERE card_id=? AND rating='correct'",
        (card_id,),
    ).fetchone()[0]
    recent_outcomes = [row[0] for row in database.execute(
        "SELECT rating FROM reviews WHERE card_id=? ORDER BY reviewed_at DESC,id DESC LIMIT 2", (card_id,)
    )]
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


def ensure_card_queued_after(database: sqlite3.Connection, card_id: str, after_cards: int = 4) -> None:
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
            "INSERT INTO daily_queue(queue_date,card_id,cycle,position,attempt_state) VALUES(?,?,?,?,?)",
            (day, card_id, cycle, 2_000_000_000, "gameplay"),
        )
        entry = database.execute("SELECT last_insert_rowid() AS id").fetchone()
    ordered_ids = [row[0] for row in database.execute(
        "SELECT id FROM daily_queue WHERE queue_date=? AND status='queued' AND id!=? ORDER BY position,id",
        (day, entry["id"]),
    )]
    ordered_ids.insert(min(after_cards, len(ordered_ids)), entry["id"])
    for position, entry_id in enumerate(ordered_ids):
        database.execute("UPDATE daily_queue SET position=? WHERE id=?", (position, entry_id))
