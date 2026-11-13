"""Retained, read-only presentation and existing Study attempt commands."""
import json

from fastapi import APIRouter, Header, HTTPException
from pydantic import TypeAdapter, ValidationError

from tempo_storage.connections import read_connection
from tempo_storage.study_attempts import storage
from .contracts.studies import ExerciseSpecification, StudyAttemptRequest, StudySelfAssessmentRequest
from .services.study_grading import validate_exercise

router = APIRouter()
_specification = TypeAdapter(ExerciseSpecification)
_prefix = "/api/studies/{study_id}/exercises/{exercise_id}"


def _decode(value):
    try:
        return _specification.validate_python(json.loads(value))
    except (ValueError, TypeError, ValidationError) as error:
        raise HTTPException(409, "Retained exercise revision is unsupported") from error


@router.get(_prefix + "/present")
def present(study_id: str, exercise_id: str, card_id: str | None = None,
            queue_entry_id: int | None = None, queue_cycle: int | None = None,
            revision: int | None = None, attempt_id: str | None = None):
    with read_connection() as database:
        queue_identity = (card_id, queue_entry_id, queue_cycle, revision)
        if any(value is not None for value in queue_identity):
            row = (storage.issued_frozen_presentation(database, study_id, exercise_id, card_id, queue_entry_id, queue_cycle, revision)
                   if all(value is not None for value in queue_identity) else None)
        elif attempt_id is not None:
            row = storage.pending_frozen_presentation(database, study_id, exercise_id, attempt_id)
        else:
            row = storage.frozen_presentation(database, study_id, exercise_id)
    if row is None:
        raise HTTPException(404, "Exercise unavailable")
    specification = _decode(row["specification_json"])
    try:
        validate_exercise(specification, row["fen"])
    except (ValueError, TypeError) as error:
        raise HTTPException(409, "Retained exercise revision is unsupported") from error
    data = specification.model_dump(mode="json")
    interaction = {
        "move_line": ("mode", "grading_policy"),
        "square_set": ("criterion", "candidate_region"),
        "knight_path": ("start_square", "target_squares", "minimum_hops", "maximum_hops", "hop_rule", "occupancy_rule"),
        "choice": ("options",),
        "explanation": (),
    }
    return {"id": row["id"], "revision": row["current_revision"], "type": data["type"],
            "prompt": data["prompt"], "hint": data["hint"], "fen": row["fen"],
            **{name: data[name] for name in interaction[data["type"]]}}


@router.post(_prefix + "/attempts")
def submit(study_id: str, exercise_id: str, request: StudyAttemptRequest,
           idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    from .command_dispatch import dispatch_command
    return dispatch_command("studies.attempts.submit", {"study_id": study_id, "exercise_id": exercise_id,
                            "attempt": request.model_dump(mode="json")},
                            idempotency_key=idempotency_key or request.attempt_id)


@router.post(_prefix + "/attempts/{attempt_id}/self-assess")
def self_assess(study_id: str, exercise_id: str, attempt_id: str, request: StudySelfAssessmentRequest,
                idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    from .command_dispatch import dispatch_command
    return dispatch_command("studies.attempts.self_assess", {"study_id": study_id, "exercise_id": exercise_id,
                            "attempt_id": attempt_id, "assessment": request.model_dump(mode="json")},
                            idempotency_key=idempotency_key or f"{attempt_id}:self-assess")


@router.get(_prefix + "/attempts/{attempt_id}/feedback")
def feedback(study_id: str, exercise_id: str, attempt_id: str):
    with read_connection() as database:
        row = storage.frozen_attempt_feedback(database, study_id, exercise_id, attempt_id)
    if row is None:
        raise HTTPException(404, "Committed attempt not found")
    return {"specification": _decode(row["specification_json"]).model_dump(mode="json")}
