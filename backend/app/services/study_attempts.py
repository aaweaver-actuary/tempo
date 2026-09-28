"""Finalize authored Study attempts and their scheduling effects."""

from __future__ import annotations

from datetime import date, datetime, timezone
import json

from fastapi import HTTPException

from .review_service import apply_scheduling_review


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def finish_study_attempt(database, attempt, rating: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    requested_rating = rating
    review_result = None
    if attempt["context"] == "review":
        card = database.execute(
            "SELECT * FROM cards WHERE id=? AND study_exercise_id=? AND archived=0 AND pending_validation=0",
            (attempt["card_id"], attempt["exercise_id"]),
        ).fetchone()
        queue = database.execute(
            "SELECT * FROM daily_queue WHERE id=? AND card_id=? AND cycle=? AND status='queued'",
            (attempt["queue_entry_id"], attempt["card_id"], attempt["cycle"]),
        ).fetchone()
        if not card or not queue or card["revision"] != attempt["revision"]:
            raise HTTPException(409, "Queue entry or card changed before completion")
        guided = bool(queue["attempt_failed"] or attempt["hint_seen"] or attempt["solution_seen_before_answer"])
        if guided:
            rating = "again"
        setting = database.execute(
            "SELECT light_first_interval_days FROM settings WHERE id=1",
        ).fetchone()
        review_result = apply_scheduling_review(
            database, card["id"], rating, guided=guided,
            source_kind="study_exercise", source_ref=attempt["id"],
            light_first_interval_days=setting[0], reviewed_at=datetime.fromisoformat(now),
            review_day=date.today(),
        )
        from ..main import requeue
        repeated_entry = (
            requeue(database, queue["queue_date"], card["id"],
                    review_result["requeue_after_cards"] or 0,
                    "guided" if rating == "again" else "reinforcement")
            if review_result["requeue_today"] else None
        )
        database.execute(
            "UPDATE daily_queue SET status='complete',attempt_state=? WHERE id=?",
            ("guided" if guided else "clean", queue["id"]),
        )
        review_result = {
            **review_result, "requeue_entry_id": repeated_entry,
            "review_id": database.execute(
                "SELECT MAX(id) FROM reviews WHERE card_id=?", (card["id"],),
            ).fetchone()[0],
        }
    result = {
        "attempt_id": attempt["id"], "rating": rating,
        "requested_rating": requested_rating, "review": review_result,
        "assessment": json.loads(attempt["assessment_json"]), "persisted": True,
    }
    database.execute(
        "UPDATE study_attempts SET finalized_at=?,result_json=? WHERE id=?",
        (now, _json(result), attempt["id"]),
    )
    if attempt["context"] == "review":
        database.execute(
            "UPDATE daily_queue SET review_result_json=? WHERE id=?",
            (_json(result), attempt["queue_entry_id"]),
        )
        exercise = database.execute(
            "SELECT * FROM study_exercises WHERE id=?", (attempt["exercise_id"],),
        ).fetchone()
        if exercise is None:
            raise HTTPException(404, "Study Exercises not found")
        siblings = database.execute(
            """SELECT id FROM study_exercises WHERE study_id=? AND id!=?
               AND (position_id=? OR (sibling_group IS NOT NULL AND sibling_group=?))""",
            (exercise["study_id"], exercise["id"], exercise["position_id"], exercise["sibling_group"]),
        )
        for sibling in siblings:
            database.execute(
                "INSERT OR IGNORE INTO study_sibling_burials VALUES(?,?,?)",
                (sibling[0], date.today().isoformat(), "answer exposure"),
            )
            database.execute(
                """UPDATE daily_queue SET status='blocked' WHERE queue_date=?
                   AND status='queued' AND card_id IN
                   (SELECT id FROM cards WHERE study_exercise_id=?)""",
                (date.today().isoformat(), sibling[0]),
            )
    return result
