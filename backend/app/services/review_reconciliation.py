"""Credit independent reviews without rewriting the completed queue attempt."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json

from .scheduler import schedule_review, unlock_ready


SCHEDULE_COLUMNS = (
    "due_date", "interval_days", "fsrs_card_json", "first_correct_at",
    "reinforcement_pending", "stability", "guided_review", "state",
    "scheduling_mode", "hard_correct_streak", "recent_attempts_json",
)


def card_schedule_state(database, card_id: str) -> dict | None:
    row = database.execute(
        f"SELECT {','.join(SCHEDULE_COLUMNS)} FROM cards WHERE id=?", (card_id,),
    ).fetchone()
    return dict(row) if row else None


def save_schedule_snapshot(database, review_id: int, state: dict) -> None:
    database.execute(
        "INSERT INTO review_schedule_snapshots(review_id,state_json) VALUES(?,?) "
        "ON CONFLICT(review_id) DO UPDATE SET state_json=excluded.state_json",
        (review_id, json.dumps(state)),
    )


def _write_card_state(database, card_id: str, state: dict) -> None:
    database.execute(
        f"UPDATE cards SET {','.join(f'{column}=?' for column in SCHEDULE_COLUMNS)} WHERE id=?",
        (*[state[column] for column in SCHEDULE_COLUMNS], card_id),
    )


def _review_day(moment: datetime, timezone_name: str) -> date:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return moment.astimezone(ZoneInfo(timezone_name)).date() if timezone_name != "local" else moment.astimezone().date()
    except ZoneInfoNotFoundError:
        return moment.astimezone().date()


def _next_state(database, card_id: str, state: dict, outcome: str, guided: bool,
                reviewed_at: datetime, review_id: int, timezone_name: str) -> tuple[dict, int]:
    settings = database.execute("SELECT light_first_interval_days FROM settings WHERE id=1").fetchone()
    schedule = schedule_review(
        outcome, interval_days=state["interval_days"], fsrs_card_json=state["fsrs_card_json"],
        first_correct_at=state["first_correct_at"],
        reinforcement_pending=bool(state["reinforcement_pending"]),
        scheduling_mode=state["scheduling_mode"], hard_correct_streak=state["hard_correct_streak"],
        recent_attempts=json.loads(state["recent_attempts_json"] or "[]"),
        light_first_interval_days=settings[0], reviewed_at=reviewed_at,
        review_day=_review_day(reviewed_at, timezone_name),
    )
    history = [dict(row) for row in database.execute(
        "SELECT id,rating,reviewed_at FROM reviews WHERE card_id=? AND invalidated_at IS NULL",
        (card_id,),
    ) if (datetime.fromisoformat(row["reviewed_at"]).astimezone(timezone.utc), row["id"])
         <= (reviewed_at.astimezone(timezone.utc), review_id)]
    history.sort(key=lambda row: (datetime.fromisoformat(row["reviewed_at"]).astimezone(timezone.utc), row["id"]))
    successful_days = len({row["reviewed_at"][:10] for row in history if row["rating"] == "correct"})
    seed = database.execute(
        "SELECT baseline_successful_days,baseline_recent_clean FROM opening_card_schedule_seeds WHERE card_id=?",
        (card_id,),
    ).fetchone()
    successful_days += int(seed["baseline_successful_days"] if seed else 0)
    recent_outcomes = [row["rating"] for row in reversed(history[-2:])]
    if seed:
        recent_outcomes.extend(["correct"] * min(int(seed["baseline_recent_clean"]), max(0, 2 - len(recent_outcomes))))
    state = {
        "due_date": schedule.due_date.isoformat(), "interval_days": schedule.interval_days,
        "fsrs_card_json": schedule.fsrs_card_json, "first_correct_at": schedule.first_correct_at,
        "reinforcement_pending": int(schedule.reinforcement_pending), "stability": schedule.stability,
        "guided_review": int(guided),
        "state": "mature" if unlock_ready(schedule.stability, successful_days, recent_outcomes) else "learning",
        "scheduling_mode": schedule.scheduling_mode, "hard_correct_streak": schedule.hard_correct_streak,
        "recent_attempts_json": json.dumps(schedule.recent_attempts),
    }
    return state, schedule.interval_days


def reconcile_completed_review(database, card_id: str, queue_entry_id: int, *,
                               attempt_id: str, outcome: str, guided: bool,
                               completed_at: datetime | None, expected_revision: int | None,
                               timezone_name: str, competing_review: dict | None = None) -> dict:
    prior = database.execute(
        "SELECT card_id,queue_entry_id,outcome,guided,completed_at,result_json "
        "FROM review_attempt_receipts WHERE attempt_id=?",
        (attempt_id,),
    ).fetchone()
    if prior:
        if (prior["card_id"] != card_id or prior["queue_entry_id"] != queue_entry_id or
                prior["outcome"] != outcome or bool(prior["guided"]) != guided or
                prior["completed_at"] != (completed_at.isoformat() if completed_at else None)):
            from fastapi import HTTPException
            raise HTTPException(409, "Review attempt ID was reused for a different result")
        return json.loads(prior["result_json"])

    current = card_schedule_state(database, card_id)
    revision_row = database.execute("SELECT revision FROM cards WHERE id=?", (card_id,)).fetchone()
    warning = None
    review_id = None
    status = "chronological"
    if not current or (expected_revision is not None and revision_row[0] != expected_revision):
        status = "history_only"
        warning = "The card changed or was removed after this review. The phone result was saved as review evidence, but the current schedule was not changed. Review the current card again when it appears to establish a new schedule."
    else:
        review_time = completed_at or datetime.now(timezone.utc)
        scheduled_outcome = "again" if guided else outcome
        later = [dict(row) for row in database.execute(
            "SELECT id,rating,guided,reviewed_at FROM reviews "
            "WHERE card_id=? AND invalidated_at IS NULL ORDER BY reviewed_at,id", (card_id,),
        ) if datetime.fromisoformat(row["reviewed_at"]).astimezone(timezone.utc) > review_time]
        later.sort(key=lambda row: (datetime.fromisoformat(row["reviewed_at"]).astimezone(timezone.utc), row["id"]))
        first_snapshot = database.execute(
            "SELECT state_json FROM review_schedule_snapshots WHERE review_id=?", (later[0]["id"],),
        ).fetchone() if later else None
        if later and first_snapshot and len(later) <= 64:
            state = json.loads(first_snapshot[0])
            initial_interval = state["interval_days"]
        else:
            state = current
            initial_interval = state["interval_days"]
            if later or completed_at is None:
                status = "computer_fallback"
                warning = "The earlier scheduling state or review time is unavailable. Both results are credited; the computer schedule was kept with a near-term review. Review this card again when it appears to establish an updated schedule."
        database.execute(
            "INSERT INTO reviews(card_id,rating,internal_rating,guided,reviewed_at,previous_interval,next_interval,source_kind,source_ref) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (card_id, scheduled_outcome, "good" if scheduled_outcome == "correct" else "again", int(guided),
             review_time.isoformat(), initial_interval, initial_interval, "study", f"attempt:{attempt_id}"),
        )
        review_id = database.execute(
            "SELECT id FROM reviews WHERE source_kind='study' AND source_ref=?", (f"attempt:{attempt_id}",),
        ).fetchone()[0]
        if status == "chronological":
            events = [(review_id, scheduled_outcome, guided, review_time)] + [
                (item["id"], item["rating"], bool(item["guided"]), datetime.fromisoformat(item["reviewed_at"]))
                for item in later
            ]
            for event_review_id, event_outcome, event_guided, event_time in events:
                save_schedule_snapshot(database, event_review_id, state)
                previous_interval = state["interval_days"]
                state, next_interval = _next_state(
                    database, card_id, state, event_outcome, event_guided, event_time,
                    event_review_id, timezone_name,
                )
                database.execute(
                    "UPDATE reviews SET previous_interval=?,next_interval=? WHERE id=?",
                    (previous_interval, next_interval, event_review_id),
                )
            _write_card_state(database, card_id, state)
            if state["state"] == "mature":
                database.execute(
                    "UPDATE cards SET state='new',due_date=? WHERE unlock_after_card_id=? AND state='locked'",
                    (_review_day(events[-1][3], timezone_name).isoformat(), card_id),
                )
        else:
            conservative_due = min(date.fromisoformat(current["due_date"]), date.today() + timedelta(days=1))
            database.execute("UPDATE cards SET due_date=? WHERE id=?", (conservative_due.isoformat(), card_id))

    result = {
        "persisted": True, "review_id": review_id, "queue_entry_id": queue_entry_id,
        "requeue_entry_id": None, "reconciliation": status, "warning": warning,
        "competing_review": competing_review,
    }
    database.execute(
        "INSERT INTO review_attempt_receipts(attempt_id,card_id,queue_entry_id,outcome,guided,completed_at,review_id,scheduling_status,warning,result_json) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (attempt_id, card_id, queue_entry_id, outcome, int(guided),
         completed_at.isoformat() if completed_at else None, review_id, status, warning, json.dumps(result)),
    )
    return result
