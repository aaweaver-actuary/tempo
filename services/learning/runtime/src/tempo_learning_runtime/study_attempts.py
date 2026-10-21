"""Finalize authored Study attempts and their scheduling effects."""

from __future__ import annotations

from datetime import datetime, timezone
from .clock import date
import json

from fastapi import HTTPException
from tempo_learning_database.adapters import study_attempts as storage

from .review_service import apply_scheduling_review


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def finish_study_attempt(database, attempt, rating: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    requested_rating = rating
    review_result = None
    if attempt["context"] == "review":
        card, queue = storage.completion_rows(database, attempt)
        if not card or not queue or card["revision"] != attempt["revision"]:
            raise HTTPException(409, "Queue entry or card changed before completion")
        guided = bool(
            queue["attempt_failed"]
            or attempt["hint_seen"]
            or attempt["solution_seen_before_answer"]
        )
        if guided:
            rating = "again"
        setting = storage.light_interval_days(database)
        review_result = apply_scheduling_review(
            database,
            card["id"],
            rating,
            guided=guided,
            source_kind="study_exercise",
            source_ref=attempt["id"],
            light_first_interval_days=setting[0],
            reviewed_at=datetime.fromisoformat(now),
            review_day=date.today(),
        )
        from .queue_application import requeue

        repeated_entry = (
            requeue(
                database,
                queue["queue_date"],
                card["id"],
                review_result["requeue_after_cards"] or 0,
                "guided" if rating == "again" else "reinforcement",
            )
            if review_result["requeue_today"]
            else None
        )
        storage.complete_queue(
            database, "guided" if guided else "clean", queue["id"],
        )
        review_result = {
            **review_result,
            "requeue_entry_id": repeated_entry,
            "review_id": storage.latest_review_id(database, card["id"]),
        }
    result = {
        "attempt_id": attempt["id"],
        "rating": rating,
        "requested_rating": requested_rating,
        "review": review_result,
        "assessment": json.loads(attempt["assessment_json"]),
        "persisted": True,
    }
    storage.finalize_attempt(database, now, _json(result), attempt["id"])
    if attempt["context"] == "review":
        storage.retain_queue_result(database, _json(result), attempt["queue_entry_id"])
        exercise = storage.exercise(database, attempt["exercise_id"])
        if exercise is None:
            raise HTTPException(404, "Study Exercises not found")
        siblings = storage.sibling_exercises(database, exercise)
        for sibling in siblings:
            storage.record_sibling_burial(
                database, sibling[0], date.today().isoformat(), "answer exposure",
            )
            storage.block_sibling_queue(
                database, date.today().isoformat(), sibling[0],
            )
    return result
