"""Credit independent reviews without rewriting the completed queue attempt."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from .clock import date
import json

from tempo_learning_database.adapters.opening_progression import unlock_legacy_children_after_review
from .scheduler import schedule_review, unlock_ready
from tempo_learning_database.adapters import review_reconciliation as storage


SCHEDULE_COLUMNS = storage.SCHEDULE_COLUMNS
card_schedule_state = storage.card_schedule_state
save_schedule_snapshot = storage.save_schedule_snapshot
_write_card_state = storage.write_card_state


def _review_day(moment: datetime, timezone_name: str) -> date:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        return (
            moment.astimezone(ZoneInfo(timezone_name)).date()
            if timezone_name != "local"
            else moment.astimezone().date()
        )
    except ZoneInfoNotFoundError:
        return moment.astimezone().date()


def _next_state(
    database,
    card_id: str,
    state: dict,
    outcome: str,
    guided: bool,
    reviewed_at: datetime,
    review_id: int,
    timezone_name: str,
) -> tuple[dict, int]:
    settings = storage.light_interval_setting(database)
    schedule = schedule_review(
        outcome,
        interval_days=state["interval_days"],
        fsrs_card_json=state["fsrs_card_json"],
        first_correct_at=state["first_correct_at"],
        reinforcement_pending=bool(state["reinforcement_pending"]),
        scheduling_mode=state["scheduling_mode"],
        hard_correct_streak=state["hard_correct_streak"],
        recent_attempts=json.loads(state["recent_attempts_json"] or "[]"),
        light_first_interval_days=settings[0],
        reviewed_at=reviewed_at,
        review_day=_review_day(reviewed_at, timezone_name),
    )
    history = [
        dict(row)
        for row in storage.valid_review_history(database, card_id)
        if (
            datetime.fromisoformat(row["reviewed_at"]).astimezone(timezone.utc),
            row["id"],
        )
        <= (reviewed_at.astimezone(timezone.utc), review_id)
    ]
    history.sort(
        key=lambda row: (
            datetime.fromisoformat(row["reviewed_at"]).astimezone(timezone.utc),
            row["id"],
        )
    )
    successful_days = len(
        {row["reviewed_at"][:10] for row in history if row["rating"] == "correct"}
    )
    seed = storage.baseline_schedule_seed(database, card_id)
    successful_days += int(seed["baseline_successful_days"] if seed else 0)
    recent_outcomes = [row["rating"] for row in reversed(history[-2:])]
    if seed:
        recent_outcomes.extend(
            ["correct"]
            * min(int(seed["baseline_recent_clean"]), max(0, 2 - len(recent_outcomes)))
        )
    state = {
        "due_date": schedule.due_date.isoformat(),
        "interval_days": schedule.interval_days,
        "fsrs_card_json": schedule.fsrs_card_json,
        "first_correct_at": schedule.first_correct_at,
        "reinforcement_pending": int(schedule.reinforcement_pending),
        "stability": schedule.stability,
        "guided_review": int(guided),
        "state": "mature"
        if unlock_ready(schedule.stability, successful_days, recent_outcomes)
        else "learning",
        "scheduling_mode": schedule.scheduling_mode,
        "hard_correct_streak": schedule.hard_correct_streak,
        "recent_attempts_json": json.dumps(schedule.recent_attempts),
    }
    return state, schedule.interval_days


def reconcile_completed_review(
    database,
    card_id: str,
    queue_entry_id: int,
    *,
    attempt_id: str,
    outcome: str,
    guided: bool,
    completed_at: datetime | None,
    expected_revision: int | None,
    timezone_name: str,
    competing_review: dict | None = None,
) -> dict:
    prior = storage.review_attempt_receipt(database, attempt_id)
    if prior:
        if (
            prior["card_id"] != card_id
            or prior["queue_entry_id"] != queue_entry_id
            or prior["outcome"] != outcome
            or bool(prior["guided"]) != guided
            or prior["completed_at"]
            != (completed_at.isoformat() if completed_at else None)
        ):
            from fastapi import HTTPException

            raise HTTPException(
                409, "Review attempt ID was reused for a different result"
            )
        return json.loads(prior["result_json"])

    current = card_schedule_state(database, card_id)
    revision_row = storage.card_revision(database, card_id)
    warning = None
    review_id = None
    status = "chronological"
    if not current or (
        expected_revision is not None and revision_row[0] != expected_revision
    ):
        status = "history_only"
        warning = "The card changed or was removed after this review. The phone result was saved as review evidence, but the current schedule was not changed. Review the current card again when it appears to establish a new schedule."
    else:
        review_time = completed_at or datetime.now(timezone.utc)
        scheduled_outcome = "again" if guided else outcome
        later = [
            dict(row)
            for row in storage.ordered_valid_reviews(database, card_id)
            if datetime.fromisoformat(row["reviewed_at"]).astimezone(timezone.utc)
            > review_time
        ]
        later.sort(
            key=lambda row: (
                datetime.fromisoformat(row["reviewed_at"]).astimezone(timezone.utc),
                row["id"],
            )
        )
        first_snapshot = (
            storage.review_snapshot(database, later[0]["id"])
            if later
            else None
        )
        if later and first_snapshot and len(later) <= 64:
            state = json.loads(first_snapshot[0])
            initial_interval = state["interval_days"]
        else:
            state = current
            initial_interval = state["interval_days"]
            if later or completed_at is None:
                status = "computer_fallback"
                warning = "The earlier scheduling state or review time is unavailable. Both results are credited; the computer schedule was kept with a near-term review. Review this card again when it appears to establish an updated schedule."
        storage.insert_independent_review(
            database, card_id=card_id, rating=scheduled_outcome,
            internal_rating="good" if scheduled_outcome == "correct" else "again",
            guided=int(guided), reviewed_at=review_time.isoformat(),
            previous_interval=initial_interval, next_interval=initial_interval,
            source_kind="study", source_ref=f"attempt:{attempt_id}",
        )
        review_id = storage.independent_review_id(database, f"attempt:{attempt_id}")
        if status == "chronological":
            events = [(review_id, scheduled_outcome, guided, review_time)] + [
                (
                    item["id"],
                    item["rating"],
                    bool(item["guided"]),
                    datetime.fromisoformat(item["reviewed_at"]),
                )
                for item in later
            ]
            for event_review_id, event_outcome, event_guided, event_time in events:
                save_schedule_snapshot(database, event_review_id, state)
                previous_interval = state["interval_days"]
                state, next_interval = _next_state(
                    database,
                    card_id,
                    state,
                    event_outcome,
                    event_guided,
                    event_time,
                    event_review_id,
                    timezone_name,
                )
                storage.update_review_intervals(database, previous_interval, next_interval, event_review_id)
            _write_card_state(database, card_id, state)
        else:
            conservative_due = min(
                date.fromisoformat(current["due_date"]),
                date.today() + timedelta(days=1),
            )
            storage.set_conservative_due(database, conservative_due.isoformat(), card_id)

    if review_id is not None:
        legacy_day = (
            _review_day(events[-1][3], timezone_name).isoformat()
            if status == "chronological"
            else date.today().isoformat()
        )
        unlock_legacy_children_after_review(
            database,
            card_id,
            legacy_day,
            state["state"] if status == "chronological" else "learning",
        )

    result = {
        "persisted": True,
        "review_id": review_id,
        "queue_entry_id": queue_entry_id,
        "requeue_entry_id": None,
        "reconciliation": status,
        "warning": warning,
        "competing_review": competing_review,
    }
    storage.retain_attempt_receipt(
        database, attempt_id=attempt_id, card_id=card_id, queue_entry_id=queue_entry_id,
        outcome=outcome, guided=int(guided), completed_at=completed_at.isoformat() if completed_at else None,
        review_id=review_id, scheduling_status=status, warning=warning, result_json=json.dumps(result),
    )
    return result
