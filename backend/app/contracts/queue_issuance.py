"""Pure identity and terminal-result policy for the existing queue receipt owner."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from .command_identity import request_digest

GENERATION_WIDTH = 12
MAX_GENERATION = 10**GENERATION_WIDTH - 1
ISSUANCE_RETRY_SECONDS = 1.0
IMMUTABLE_CONFLICTS = frozenset(
    {
        "position_attempt_payload_conflict",
        "position_attempt_identity_conflict",
        "position_issued_row_mismatch",
        "position_issued_row_invalid",
    }
)


def issuance_operation_id(payload: dict, generation: int = 0) -> str:
    if type(generation) is not int or not 0 <= generation <= MAX_GENERATION:
        raise ValueError("Queue issuance retry generation is exhausted or invalid")
    if set(payload) != {"queue_date", "entry_id", "card_id", "expected_revision"}:
        raise ValueError("Queue issuance requires the exact original head identity")
    return f"queue.issue_head:v1:{request_digest('queue.issue_head', payload)}:{generation:012d}"


def issuance_generation(operation_id: str, payload: dict) -> int:
    first = issuance_operation_id(payload)
    prefix = first[:-GENERATION_WIDTH]
    suffix = operation_id[len(prefix) :]
    if (
        not operation_id.startswith(prefix)
        or len(suffix) != GENERATION_WIDTH
        or not suffix.isascii()
        or not suffix.isdigit()
    ):
        raise ValueError("Invalid queue issuance receipt family")
    return int(suffix)


def immutable_issuance_conflict(response: dict) -> str | None:
    reason = response.get("position_study_hold")
    return reason if reason in IMMUTABLE_CONFLICTS else None
# Internal Study application outcomes, not new HTTP response bodies. The queue
# envelope remains opaque here; only the serving owner can establish currentness.
class NextExerciseReady(TypedDict):
    kind: Literal["ready"]
    queue: dict[str, Any]


class NextExerciseEmpty(TypedDict):
    kind: Literal["empty"]
    queue: dict[str, Any]


class NextExercisePending(TypedDict):
    kind: Literal["pending"]
    operation_id: str


class NextExerciseHeld(TypedDict):
    kind: Literal["held"]
    reason: str
    operation_id: NotRequired[str]
    queue: NotRequired[dict[str, Any]]


class NextExerciseUnavailable(TypedDict):
    kind: Literal["unavailable"]
    reason: str
    retryable: bool | None
    queue: NotRequired[dict[str, Any]]


NextExerciseOutcome = (
    NextExerciseReady | NextExerciseEmpty | NextExercisePending
    | NextExerciseHeld | NextExerciseUnavailable
)
