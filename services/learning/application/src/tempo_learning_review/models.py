"""Internal immutable records, independent of HTTP models and wire v1."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
import json
from typing import Literal


@dataclass(frozen=True)
class PreparedNext:
    queue_entry_id: int
    card_id: str
    revision: int
    cycle: int
    local_date: str

    def as_dict(self):
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True)
class EvidenceCompletion:
    attempt_id: str
    card_id: str
    queue_entry_id: int | None
    complete: bool
    card_revision: int
    checkpoint_json: str


@dataclass(frozen=True)
class ReviewCommand:
    card_id: str
    outcome: Literal["correct", "again"]
    guided: bool
    queue_entry_id: int | None
    recorded_at: str | None
    expected_review_id: int | None
    expected_revision: int | None
    attempt_id: str | None
    payload_json: str
    prepared_next: PreparedNext | None = None
    completion: EvidenceCompletion | None = None


@dataclass(frozen=True)
class ReviewContext:
    reviewed_at: datetime
    local_day: date
    review_day: date


@dataclass(frozen=True)
class ReviewPolicy:
    timezone: str
    light_first_interval_days: int


@dataclass(frozen=True)
class CardFacts:
    content_type: str
    archived: bool
    superseded_by: str | None
    revision: int
    state: str
    integrity_allowed: bool
    owner_ids: tuple[str, ...]


@dataclass(frozen=True)
class AttemptReceipt:
    card_id: str
    queue_entry_id: int
    outcome: Literal["correct", "again"]
    guided: bool
    completed_at: str | None
    result_json: str
    request_json: str | None


@dataclass(frozen=True)
class QueueAttempt:
    id: int
    card_id: str
    queue_date: str
    cycle: int
    admission_kind: str
    revision: int
    attempt_failed: bool
    status: str
    review_result_json: str | None
    recovered: bool = False


@dataclass(frozen=True)
class ContinuationFacts:
    cycle: int
    revision: int
    content_type: str
    attempt_failed: bool


@dataclass(frozen=True)
class CompetingReview:
    outcome: str | None
    completed_at: str | None

    def as_dict(self) -> dict:
        return {"outcome": self.outcome, "completed_at": self.completed_at}


@dataclass(frozen=True)
class PreparedContinuation:
    candidate: PreparedNext
    attempt_failed: bool

    def as_dict(self) -> dict:
        return {**self.candidate.as_dict(), "attempt_failed": self.attempt_failed}


@dataclass(frozen=True)
class OriginalReviewReceipt:
    """Opaque saved response: its bytes remain authoritative on replay."""
    payload_json: str


@dataclass(frozen=True)
class CurrentReviewResult:
    persisted: bool
    queue_entry_id: int
    review_id: int | None
    requeue_entry_id: int | None
    idempotent: bool | None
    reconciliation: str | None


@dataclass(frozen=True)
class ReviewResult:
    payload_json: str
    current: CurrentReviewResult | None = field(default=None, compare=False)
    original: OriginalReviewReceipt | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, result: dict) -> ReviewResult:
        for name in ("persisted", "queue_entry_id", "review_id"):
            if name not in result:
                raise ValueError(f"Missing review result field: {name}")
        if (result["persisted"] is not True or type(result["queue_entry_id"]) is not int
            or (result["review_id"] is not None and type(result["review_id"]) is not int)
            or (result.get("requeue_entry_id") is not None and type(result["requeue_entry_id"]) is not int)
            or (result.get("idempotent") is not None and type(result["idempotent"]) is not bool)
            or (result.get("idempotent") is False and result["review_id"] is None)):
            raise ValueError("Invalid review result identity or disposition")
        current = CurrentReviewResult(
            result["persisted"], result["queue_entry_id"], result["review_id"],
            result.get("requeue_entry_id"), result.get("idempotent"), result.get("reconciliation"),
        )
        return cls(json.dumps(result), current=current)

    @classmethod
    def from_original(cls, payload_json: str) -> ReviewResult:
        return cls(payload_json, original=OriginalReviewReceipt(payload_json))

    def as_dict(self) -> dict:
        return json.loads(self.payload_json)


@dataclass(frozen=True)
class ScheduledReview:
    card_id: str
    next_due: str
    interval_days: int
    state: str
    requeue_today: bool
    requeue_after_cards: int | None
    stability: float
    scheduling_mode: str
    hard_correct_streak: int
    suggest_shorter_prefix: bool
    idempotent: bool

    @classmethod
    def from_dict(cls, value: dict) -> ScheduledReview:
        return cls(**value)

    def as_dict(self) -> dict:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True)
class ScheduleSnapshot:
    """Original snapshot fields in their original insertion order."""
    due_date: str
    interval_days: int
    fsrs_card_json: str | None
    first_correct_at: str | None
    reinforcement_pending: int | bool
    stability: float
    guided_review: int | bool
    state: str
    scheduling_mode: str
    hard_correct_streak: int
    recent_attempts_json: str | None
    payload_json: str

    @classmethod
    def from_dict(cls, value: dict) -> ScheduleSnapshot:
        for field in ("due_date", "interval_days", "fsrs_card_json", "first_correct_at",
                      "reinforcement_pending", "stability", "guided_review", "state",
                      "scheduling_mode", "hard_correct_streak", "recent_attempts_json"):
            if field not in value:
                raise ValueError(f"Missing schedule snapshot field: {field}")
        return cls(
            value["due_date"], value["interval_days"], value["fsrs_card_json"],
            value["first_correct_at"], value["reinforcement_pending"], value["stability"],
            value["guided_review"], value["state"], value["scheduling_mode"],
            value["hard_correct_streak"], value["recent_attempts_json"], json.dumps(value),
        )

    def as_dict(self) -> dict:
        return json.loads(self.payload_json)


@dataclass(frozen=True)
class ReconciledReview:
    """Retained result shape, including optional owner-provided metadata."""
    persisted: bool
    review_id: int | None
    queue_entry_id: int
    requeue_entry_id: int | None
    reconciliation: str
    warning: str | None
    competing_review: dict[str, str | None] | None
    payload_json: str

    @classmethod
    def from_dict(cls, value: dict) -> ReconciledReview:
        for field in ("persisted", "review_id", "queue_entry_id", "requeue_entry_id", "reconciliation"):
            if field not in value:
                raise ValueError(f"Missing reconciliation field: {field}")
        return cls(
            value["persisted"], value["review_id"], value["queue_entry_id"],
            value["requeue_entry_id"], value["reconciliation"], value.get("warning"),
            value.get("competing_review"), json.dumps(value),
        )

    def as_dict(self) -> dict:
        return json.loads(self.payload_json)
