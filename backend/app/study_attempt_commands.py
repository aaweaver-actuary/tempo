"""Foreground Study attempt commands with PostgreSQL replay and queue locks."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from typing import Any

from fastapi import HTTPException
from pydantic import TypeAdapter

from .command_gateway import register_command
from tempo_legacy_database.adapters.study_attempts import storage
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from .queue_commands import request_queue_refresh_in_transaction
from tempo_legacy_database.adapters.queue_position_lock import lock_queue_date_for_position
from .services.study_grading import GRADER_VERSION, evaluate_answer
from .services.study_attempts import finish_study_attempt
from app.contracts.studies import ExerciseSpecification, StudyAttemptRequest, StudySelfAssessmentRequest


_SPECIFICATION_ADAPTER = TypeAdapter(ExerciseSpecification)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _lock_review_queue(database: PostgresConnection, request: StudyAttemptRequest,
                       exercise_id: str):
    card = storage.locked_review_card(database, request.card_id, exercise_id)
    if card is None:
        raise HTTPException(409, "The selected exercise is no longer queued")
    queue_day = storage.queue_day(database, request.queue_entry_id)
    if queue_day is None:
        raise HTTPException(409, "The selected exercise is no longer queued")
    lock_queue_date_for_position(database, queue_day[0])
    queue = storage.locked_review_entry(database, request.queue_entry_id, request.card_id, request.queue_cycle)
    if queue is None or card["revision"] != request.revision:
        raise HTTPException(409, "The selected exercise is no longer queued")
    if storage.answered_queue_entry(database, queue["id"]):
        raise HTTPException(409, "This queue attempt already has an answer")
    if request.expected_review_id is not None:
        latest = storage.latest_effective_review_id(database, card["id"])
        if latest != request.expected_review_id:
            raise HTTPException(409, "Another device already reviewed this card")
    return queue


def submit_study_attempt(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    request = StudyAttemptRequest.model_validate(payload["attempt"])
    if len(request.attempt_id) > 100:
        raise HTTPException(422, "Attempt ID is too long")
    storage.lock_attempt_identity(database, request.attempt_id)
    request_data = request.model_dump(mode="json")
    answer_hash = hashlib.sha256(_json({"request": request_data}).encode()).hexdigest()
    existing = storage.attempt(database, request.attempt_id)
    if existing:
        owner = storage.exercise_owner(database, existing["exercise_id"])
        if (existing["answer_hash"] != answer_hash
                or existing["exercise_id"] != str(payload["exercise_id"])
                or owner is None or owner[0] != str(payload["study_id"])):
            raise HTTPException(409, "Attempt ID was already used with another response")
        return json.loads(existing["result_json"]) if existing["result_json"] else {
            "attempt_id": existing["id"],
            "assessment": json.loads(existing["assessment_json"]),
            "pending_self_assessment": True,
        }
    exercise_id = str(payload["exercise_id"])
    exercise = storage.locked_exercise(database, exercise_id)
    if exercise is None:
        raise HTTPException(404, "Study Exercises not found")
    if (exercise["study_id"] != str(payload["study_id"])
            or exercise["status"] == "archived"
            or exercise["current_revision"] != request.revision):
        raise HTTPException(409, "Exercise revision changed; reload before answering")
    position = storage.exercise_position(database, exercise["position_id"])
    if position is None:
        raise HTTPException(404, "Study Positions not found")
    revision = storage.exercise_revision(database, exercise_id, exercise["current_revision"])
    if revision is None:
        raise HTTPException(409, "Exercise revision is unavailable")
    specification = _SPECIFICATION_ADAPTER.validate_python(json.loads(revision[0]))
    assessment = evaluate_answer(specification, request.answer, position["fen"])
    if assessment["outcome"] == "invalid_submission":
        raise HTTPException(422, assessment["feedback"])
    if request.context == "review":
        _lock_review_queue(database, request, exercise_id)
    elif any(value is not None for value in
             (request.card_id, request.queue_entry_id, request.queue_cycle)):
        raise HTTPException(422, "Practice attempts cannot name a queue entry")
    now = datetime.now(timezone.utc).isoformat()
    storage.insert_attempt(database, (request.attempt_id, exercise_id, request.revision, request.card_id,
         request.queue_entry_id, request.queue_cycle, request.context,
         _json(request.answer.model_dump(mode="json")), answer_hash, _json(assessment),
         "self_assessed" if assessment["outcome"] in {"unrecognized", "needs_self_assessment"}
         else "automatic", GRADER_VERSION, int(request.hint_seen),
         int(request.solution_seen_before_answer), request.started_at or now, now))
    if assessment["outcome"] in {"unrecognized", "needs_self_assessment"}:
        return {"attempt_id": request.attempt_id, "assessment": assessment,
                "pending_self_assessment": True}
    persisted_attempt = storage.attempt(database, request.attempt_id)
    result = finish_study_attempt(
        database, persisted_attempt,
        "correct" if assessment["outcome"] == "correct" else "again",
    )
    request_queue_refresh_in_transaction(database, date.today().isoformat())
    return result


def self_assess_study_attempt(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    request = StudySelfAssessmentRequest.model_validate(payload["assessment"])
    attempt_id = str(payload["attempt_id"])
    if len(attempt_id) > 100:
        raise HTTPException(422, "Attempt ID is too long")
    pending_attempt = storage.pending_attempt(database, attempt_id)
    if pending_attempt is None:
        raise HTTPException(404, "Attempt not found")
    if pending_attempt["context"] == "review":
        storage.lock_attempt_card(database, pending_attempt["card_id"])
        queue_day = storage.queue_day(database, pending_attempt["queue_entry_id"])
        if queue_day is None:
            raise HTTPException(409, "Queue entry or card changed before completion")
        lock_queue_date_for_position(database, queue_day[0])
        storage.lock_attempt_queue_entry(database, pending_attempt["queue_entry_id"])
    attempt = storage.locked_attempt(database, attempt_id, str(payload["exercise_id"]))
    exercise = storage.exercise(database, str(payload["exercise_id"]))
    if (exercise is None or exercise["study_id"] != str(payload["study_id"])
            or attempt is None):
        raise HTTPException(404, "Attempt not found")
    if attempt["result_json"]:
        result = json.loads(attempt["result_json"])
        if result.get("requested_rating", result["rating"]) != request.rating:
            raise HTTPException(409, "Attempt already finalized with a different rating")
        return result
    if attempt["retired_operation_id"]:
        raise HTTPException(409, "The original presentation was retired before self-assessment")
    if exercise["current_revision"] != attempt["revision"]:
        raise HTTPException(409, "Exercise changed before self-assessment")
    assessment = json.loads(attempt["assessment_json"])
    if assessment["outcome"] not in {"unrecognized", "needs_self_assessment"}:
        raise HTTPException(409, "This attempt does not need self-assessment")
    result = finish_study_attempt(database, attempt, request.rating)
    request_queue_refresh_in_transaction(database, date.today().isoformat())
    return result


register_command("studies.attempts.submit", submit_study_attempt)
register_command("studies.attempts.self_assess", self_assess_study_attempt)
