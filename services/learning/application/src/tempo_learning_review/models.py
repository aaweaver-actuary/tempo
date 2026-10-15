"""Internal immutable records, independent of HTTP models and wire v1."""
from __future__ import annotations
from dataclasses import dataclass
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
class ReviewResult:
    payload_json: str

    @classmethod
    def from_dict(cls, result: dict) -> ReviewResult:
        return cls(json.dumps(result))

    def as_dict(self) -> dict:
        return json.loads(self.payload_json)
