"""Learning authority: one local transaction settles every required effect."""

from typing import Annotated, Literal
from pydantic import Field, model_validator
from ._wire import (
    Model,
    WireModel,
    Identifier,
    Revision,
    Count,
    Timestamp,
    UciMove,
    Square,
    serialize_contract,
    validate_contract,
)
from ._exercise import ExecutableExercise


class MoveAnswer(Model):
    type: Literal["move_line"]
    moves: list[UciMove] = Field(min_length=1, max_length=40)


class SquareAnswer(Model):
    type: Literal["square_set"]
    squares: list[Square] = Field(max_length=64)


class KnightAnswer(Model):
    type: Literal["knight_path"]
    reachable: bool
    path: list[Square] = Field(max_length=4)


class ChoiceAnswer(Model):
    type: Literal["choice"]
    option_ids: list[Identifier] = Field(min_length=1, max_length=12)


class ExplanationAnswer(Model):
    type: Literal["explanation"]
    text: Annotated[str, Field(max_length=10000)]
    ready: bool


Answer = Annotated[
    MoveAnswer | SquareAnswer | KnightAnswer | ChoiceAnswer | ExplanationAnswer,
    Field(discriminator="type"),
]


class AttemptRequest(WireModel):
    workspace_id: Identifier
    request_id: Identifier
    card_id: Identifier
    expected_publication_generation: Revision
    expected_learning_revision: Revision


class IssuedAttempt(Model):
    attempt_id: Identifier
    attempt_revision: Revision
    queue_entry_id: Identifier
    queue_revision: Revision
    issued_at: Timestamp
    exercise: ExecutableExercise


class AttemptIssuance(WireModel):
    workspace_id: Identifier
    request_id: Identifier
    learning_revision: Revision
    attempt: IssuedAttempt


class ReviewSubmission(WireModel):
    workspace_id: Identifier
    submission_id: Identifier
    attempt_id: Identifier
    attempt_revision: Revision
    card_id: Identifier
    publication_id: Identifier
    publication_generation: Revision
    exercise_revision: Revision
    queue_entry_id: Identifier
    queue_revision: Revision
    answer: Answer
    outcome: Literal["correct", "again"]
    guided: bool
    recorded_at: Timestamp


class ReviewReceipt(Model):
    receipt_id: Identifier
    submission_id: Identifier
    attempt_id: Identifier
    card_id: Identifier
    publication_generation: Revision
    committed_at: Timestamp
    learning_revision: Revision
    queue_revision: Revision
    next_due: Timestamp
    review_id: Identifier
    required_evidence_ids: list[Identifier]


class Received(Model):
    status: Literal["received"]


class Pending(Model):
    status: Literal["accepted_pending"]
    operation_id: Identifier


class Committed(Model):
    status: Literal["committed"]
    receipt: ReviewReceipt


class RetryableFailure(Model):
    status: Literal["retryable_failure"]
    code: Identifier
    message: Identifier
    retry_after_ms: Count


class TerminalFailure(Model):
    status: Literal["terminal_failure"]
    code: Identifier
    message: Identifier


class Conflict(Model):
    status: Literal["conflict"]
    code: Literal["identity_payload_conflict", "concurrent_review_conflict"]
    message: Identifier


class Stale(Model):
    status: Literal["stale_attempt_version"]
    code: Literal[
        "retired_publication",
        "superseded_attempt",
        "publication_generation_changed",
        "queue_revision_changed",
    ]
    current_learning_revision: Revision


Settlement = Annotated[
    Received
    | Pending
    | Committed
    | RetryableFailure
    | TerminalFailure
    | Conflict
    | Stale,
    Field(discriminator="status"),
]


class ReviewSettlement(WireModel):
    workspace_id: Identifier
    submission_id: Identifier
    settlement: Settlement

    @model_validator(mode="after")
    def bind_receipt(self):
        if (
            isinstance(self.settlement, Committed)
            and self.settlement.receipt.submission_id != self.submission_id
        ):
            raise ValueError("receipt belongs to another submission")
        return self


class ReceiptQuery(WireModel):
    workspace_id: Identifier
    submission_id: Identifier


class QueueRequest(WireModel):
    workspace_id: Identifier
    request_id: Identifier
    expected_queue_revision: Revision
    requested_capacity: Annotated[int, Field(ge=1, le=100)]


class QueueIssuance(WireModel):
    workspace_id: Identifier
    request_id: Identifier
    queue_revision: Revision
    status: Literal["issued", "accepted_pending", "empty", "retryable_failure"]
    attempts: list[IssuedAttempt] = Field(max_length=100)
    reason: Identifier | None

    @model_validator(mode="after")
    def truthful_issuance(self):
        if (self.status == "issued") != bool(self.attempts):
            raise ValueError("only issued queues carry attempts")
        if (
            self.status in ("accepted_pending", "retryable_failure")
            and self.reason is None
        ):
            raise ValueError("pending/failure requires a reason")
        if len({attempt.attempt_id for attempt in self.attempts}) != len(self.attempts):
            raise ValueError("duplicate attempt")
        return self


class LearningObligation(WireModel):
    workspace_id: Identifier
    obligation_id: Identifier
    source_kind: Literal["game_evidence"]
    source_evidence_id: Identifier
    source_revision: Revision
    card_id: Identifier
    decision_id: Identifier
    publication_generation: Revision
    kind: Literal["real_game_miss", "observed_encounter"]


class LearningState(WireModel):
    workspace_id: Identifier
    learning_revision: Revision
    queue_revision: Revision
    active_publication_generations: dict[Identifier, Revision]
    pending_obligation_ids: list[Identifier]
    deterministic_introductions_available: bool


PUBLIC_MODELS = (
    AttemptRequest,
    AttemptIssuance,
    ReviewSubmission,
    ReviewSettlement,
    ReceiptQuery,
    QueueRequest,
    QueueIssuance,
    LearningObligation,
    LearningState,
)

__all__ = [
    "MoveAnswer",
    "SquareAnswer",
    "KnightAnswer",
    "ChoiceAnswer",
    "ExplanationAnswer",
    "AttemptRequest",
    "IssuedAttempt",
    "AttemptIssuance",
    "ReviewSubmission",
    "ReviewReceipt",
    "Received",
    "Pending",
    "Committed",
    "RetryableFailure",
    "TerminalFailure",
    "Conflict",
    "Stale",
    "ReviewSettlement",
    "ReceiptQuery",
    "QueueRequest",
    "QueueIssuance",
    "LearningObligation",
    "LearningState",
    "PUBLIC_MODELS",
    "serialize_contract",
    "validate_contract",
    "ExecutableExercise",
]
