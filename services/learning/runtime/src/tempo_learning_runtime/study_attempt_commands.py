"""Foreground Study attempt commands with PostgreSQL replay and queue locks."""

from __future__ import annotations

from datetime import datetime, timezone
from .clock import date
import hashlib
import json
from typing import Any

from fastapi import HTTPException
from pydantic import TypeAdapter

from .gateway import register_command
from tempo_learning_database.adapters.postgres_store import PostgresConnection
from .queue_commands import request_queue_refresh_in_transaction
from tempo_learning_database.adapters.queue_position_lock import lock_queue_date_for_position
from tempo_learning_database.adapters import study_attempt_commands as storage
from .study_grading import GRADER_VERSION, evaluate_answer
from .study_attempts import finish_study_attempt
from .study_contracts import (
    ExerciseSpecification,
    StudyAttemptRequest,
    StudySelfAssessmentRequest,
)


_SPECIFICATION_ADAPTER = TypeAdapter(ExerciseSpecification)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _lock_review_queue(
    database: PostgresConnection, request: StudyAttemptRequest, exercise_id: str
):
    card = storage.review_card_for_update(database, request.card_id, exercise_id)
    if card is None:
        raise HTTPException(409, "The selected exercise is no longer queued")
    queue_day = storage.review_queue_date(database, request.queue_entry_id)
    if queue_day is None:
        raise HTTPException(409, "The selected exercise is no longer queued")
    lock_queue_date_for_position(database, queue_day[0])
    queue = storage.review_queue_for_update(database, request.queue_entry_id, request.card_id, request.queue_cycle)
    if queue is None or card["revision"] != request.revision:
        raise HTTPException(409, "The selected exercise is no longer queued")
    if storage.existing_queue_answer(database, queue["id"]):
        raise HTTPException(409, "This queue attempt already has an answer")
    if request.expected_review_id is not None:
        latest = storage.latest_review_id(database, card["id"])
        if latest != request.expected_review_id:
            raise HTTPException(409, "Another device already reviewed this card")
    return queue


def submit_study_attempt(
    database: PostgresConnection, payload: dict[str, Any]
) -> dict[str, Any]:
    request = StudyAttemptRequest.model_validate(payload["attempt"])
    if len(request.attempt_id) > 100:
        raise HTTPException(422, "Attempt ID is too long")
    storage.lock_attempt_identity(database, request.attempt_id)
    request_data = request.model_dump(mode="json")
    answer_hash = hashlib.sha256(_json({"request": request_data}).encode()).hexdigest()
    existing = storage.attempt_by_id(database, request.attempt_id)
    if existing:
        owner = storage.exercise_owner(database, existing["exercise_id"])
        if (
            existing["answer_hash"] != answer_hash
            or existing["exercise_id"] != str(payload["exercise_id"])
            or owner is None
            or owner[0] != str(payload["study_id"])
        ):
            raise HTTPException(
                409, "Attempt ID was already used with another response"
            )
        return (
            json.loads(existing["result_json"])
            if existing["result_json"]
            else {
                "attempt_id": existing["id"],
                "assessment": json.loads(existing["assessment_json"]),
                "pending_self_assessment": True,
            }
        )
    exercise_id = str(payload["exercise_id"])
    exercise = storage.exercise_for_update(database, exercise_id)
    if exercise is None:
        raise HTTPException(404, "Study Exercises not found")
    if (
        exercise["study_id"] != str(payload["study_id"])
        or exercise["status"] == "archived"
        or exercise["current_revision"] != request.revision
    ):
        raise HTTPException(409, "Exercise revision changed; reload before answering")
    if not storage.serving_exercise_card(database, exercise_id):
        raise HTTPException(409, "Exercise publication was retired")
    position = storage.exercise_position(database, exercise["position_id"])
    if position is None:
        raise HTTPException(404, "Study Positions not found")
    revision = storage.revision_specification(database, exercise_id, exercise["current_revision"])
    if revision is None:
        raise HTTPException(409, "Exercise revision is unavailable")
    specification = _SPECIFICATION_ADAPTER.validate_python(json.loads(revision[0]))
    assessment = evaluate_answer(specification, request.answer, position["fen"])
    if assessment["outcome"] == "invalid_submission":
        raise HTTPException(422, assessment["feedback"])
    if request.context == "review":
        _lock_review_queue(database, request, exercise_id)
    elif any(
        value is not None
        for value in (request.card_id, request.queue_entry_id, request.queue_cycle)
    ):
        raise HTTPException(422, "Practice attempts cannot name a queue entry")
    now = datetime.now(timezone.utc).isoformat()
    storage.record_attempt(
        database, attempt_id=request.attempt_id, exercise_id=exercise_id,
        revision=request.revision, card_id=request.card_id,
        queue_entry_id=request.queue_entry_id, cycle=request.queue_cycle,
        context=request.context, answer_json=_json(request.answer.model_dump(mode="json")),
        answer_hash=answer_hash, assessment_json=_json(assessment),
        assessment_method="self_assessed"
        if assessment["outcome"] in {"unrecognized", "needs_self_assessment"}
        else "automatic",
        grader_version=GRADER_VERSION, hint_seen=int(request.hint_seen),
        solution_seen_before_answer=int(request.solution_seen_before_answer),
        started_at=request.started_at or now, committed_at=now,
    )
    if assessment["outcome"] in {"unrecognized", "needs_self_assessment"}:
        return {
            "attempt_id": request.attempt_id,
            "assessment": assessment,
            "pending_self_assessment": True,
        }
    persisted_attempt = storage.attempt_by_id(database, request.attempt_id)
    result = finish_study_attempt(
        database,
        persisted_attempt,
        "correct" if assessment["outcome"] == "correct" else "again",
    )
    request_queue_refresh_in_transaction(database, date.today().isoformat())
    return result


def self_assess_study_attempt(
    database: PostgresConnection, payload: dict[str, Any]
) -> dict[str, Any]:
    request = StudySelfAssessmentRequest.model_validate(payload["assessment"])
    attempt_id = str(payload["attempt_id"])
    if len(attempt_id) > 100:
        raise HTTPException(422, "Attempt ID is too long")
    pending_attempt = storage.pending_assessment_attempt(database, attempt_id)
    if pending_attempt is None:
        raise HTTPException(404, "Attempt not found")
    if pending_attempt["context"] == "review":
        storage.lock_assessment_card(database, pending_attempt["card_id"])
        queue_day = storage.review_queue_date(database, pending_attempt["queue_entry_id"])
        if queue_day is None:
            raise HTTPException(409, "Queue entry or card changed before completion")
        lock_queue_date_for_position(database, queue_day[0])
        storage.lock_assessment_queue(database, pending_attempt["queue_entry_id"])
    attempt = storage.assessment_attempt_for_update(database, attempt_id, str(payload["exercise_id"]))
    exercise = storage.assessment_exercise(database, str(payload["exercise_id"]))
    if (
        exercise is None
        or exercise["study_id"] != str(payload["study_id"])
        or attempt is None
    ):
        raise HTTPException(404, "Attempt not found")
    if attempt["result_json"]:
        result = json.loads(attempt["result_json"])
        if result.get("requested_rating", result["rating"]) != request.rating:
            raise HTTPException(
                409, "Attempt already finalized with a different rating"
            )
        return result
    if attempt["retired_operation_id"]:
        raise HTTPException(
            409, "The original presentation was retired before self-assessment"
        )
    if exercise["current_revision"] != attempt["revision"]:
        raise HTTPException(409, "Exercise changed before self-assessment")
    assessment = json.loads(attempt["assessment_json"])
    if assessment["outcome"] not in {"unrecognized", "needs_self_assessment"}:
        raise HTTPException(409, "This attempt does not need self-assessment")
    result = finish_study_attempt(database, attempt, request.rating)
    request_queue_refresh_in_transaction(database, date.today().isoformat())
    return result


def register_handlers():
    register_command("studies.attempts.submit", submit_study_attempt)
    register_command("studies.attempts.self_assess", self_assess_study_attempt)
