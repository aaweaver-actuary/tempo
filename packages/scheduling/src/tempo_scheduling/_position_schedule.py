"""Pure, position-only FSRS transitions; the Study writer owns authorization.

The caller allocates IDs and establishes uniqueness across its persisted FSRS
states. This module neither queries a database nor enforces daily grade credit.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from importlib.metadata import version
import json
import math
from typing import Literal, NamedTuple

from fsrs import Card, Rating, Scheduler, State

from ._scheduler import DESIRED_RETENTION, MAXIMUM_INTERVAL_DAYS

_FSRS_VERSION = version("fsrs")
_CARD_FIELDS = {"card_id", "state", "step", "stability", "difficulty", "due", "last_review"}
# Presentation-level legacy flags are deliberately absent from the cloned Card.
_PHYSICAL_FIELDS = {
    "first_correct_at", "reinforcement_pending", "scheduling_mode",
    "hard_correct_streak", "recent_attempts", "requeue_today",
    "requeue_after_cards", "suggest_shorter_prefix", "light_first_interval_days",
}
PositionGrade = Literal["good", "again"]


class InitialPositionState(NamedTuple):
    fsrs_card_json: str
    due_at: datetime


def initial_position_state(allocated_card_id: int, due_at: datetime) -> InitialPositionState:
    """Construct fresh Learning state with the caller's allocated ID and instant.

    Allocation, uniqueness, admission and persistence remain with the Study
    transaction. No review or clock-derived identity is manufactured here.
    """
    if not _positive_id(allocated_card_id):
        raise ValueError("allocated_card_id must be a positive integer")
    if not isinstance(due_at, datetime) or due_at.utcoffset() is None:
        raise ValueError("due_at must be timezone-aware")
    if _FSRS_VERSION != "6.3.2":
        raise ValueError("unsupported FSRS version; expected 6.3.2")
    card = Card(card_id=allocated_card_id, due=due_at)
    return InitialPositionState(card.to_json(), card.due)


@dataclass(frozen=True)
class PositionSchedule:
    fsrs_card_json: str
    due_at: datetime
    interval_days: int
    due_date: date
    stability: float
    internal_rating: PositionGrade
    # Position cohorts never inherit physical-card reinforcement/tracks.
    first_correct_at: None = None
    reinforcement_pending: bool = False
    scheduling_mode: Literal["normal"] = "normal"
    hard_correct_streak: int = 0
    requeue_today: bool = False


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate card field")
        result[key] = value
    return result


def _positive_id(value: object) -> bool:
    return type(value) is int and value > 0


def _validated_card(fsrs_json: str) -> Card:
    if _FSRS_VERSION != "6.3.2":
        raise ValueError("unsupported FSRS version; expected 6.3.2")
    try:
        if not isinstance(fsrs_json, str):
            raise ValueError("serialized card state required")
        raw = json.loads(fsrs_json, object_pairs_hook=_unique_fields)
        if not isinstance(raw, dict) or not _CARD_FIELDS <= raw.keys():
            raise ValueError("complete card state required")
        if raw.keys() - _CARD_FIELDS - _PHYSICAL_FIELDS:
            raise ValueError("unsupported card fields or versioned envelope")
        if not _positive_id(raw["card_id"]):
            raise ValueError("positive integer card_id required")
        if type(raw["state"]) is not int or raw["state"] not in (1, 2, 3):
            raise ValueError("unsupported card state")
        if raw["state"] == State.Review.value:
            if raw["step"] is not None:
                raise ValueError("Review step must be null")
        elif type(raw["step"]) is not int or raw["step"] < 0:
            raise ValueError("Learning/Relearning step must be a nonnegative integer")
        times = {}
        for field in ("due", "last_review"):
            value = raw[field]
            if field == "last_review" and value is None:
                times[field] = None
                continue
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field} must be an aware timestamp")
            timestamp = datetime.fromisoformat(value)
            if timestamp.utcoffset() is None:
                raise ValueError(f"{field} must be timezone-aware")
            times[field] = timestamp
        stability, difficulty = raw["stability"], raw["difficulty"]
        initial = stability is None and difficulty is None and times["last_review"] is None
        if initial:
            if raw["state"] != State.Learning.value or raw["step"] != 0:
                raise ValueError("initial card must be Learning step zero")
        else:
            for field, value in (("stability", stability), ("difficulty", difficulty)):
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError(f"finite {field} required")
            if stability <= 0 or not 1 <= difficulty <= 10 or times["last_review"] is None:
                raise ValueError("incoherent reviewed memory")
            if times["due"] < times["last_review"]:
                raise ValueError("due precedes last_review")
        # Only supported FSRS Card decoding/serialization; never manufacture reviews.
        card = Card.from_dict({field: raw[field] for field in _CARD_FIELDS})
        roundtrip = Card.from_json(card.to_json())
        if card.to_dict() != roundtrip.to_dict():
            raise ValueError("Card roundtrip changed memory")
        return roundtrip
    except (TypeError, ValueError, KeyError, OverflowError) as error:
        raise ValueError(f"invalid position fsrs_json: {error}") from error


def clone_state(fsrs_json: str, *, allocated_new_card_id: int) -> str:
    """Clone one donor's memory with an externally allocated, different identity.

    The caller must check global persisted uniqueness, including legacy cards.
    Physical-card metadata is discarded, not inherited or replayed. Donor
    provenance belongs alongside this blob in the owning persistence layer.
    """
    if not _positive_id(allocated_new_card_id):
        raise ValueError("allocated_new_card_id must be a positive integer")
    card = _validated_card(fsrs_json)
    if allocated_new_card_id == card.card_id:
        raise ValueError("allocated_new_card_id must differ from donor card_id")
    card.card_id = allocated_new_card_id
    return card.to_json()


def schedule_position_review(
    grade: PositionGrade,
    *,
    fsrs_card_json: str,
    accepted_at: datetime,
    review_day: date,
) -> PositionSchedule:
    """Apply one authorized Good/Again at acceptance, without reinforcement.

    Receipt replay, due-at-issuance, revision/epoch fences and once-per-local-day
    positive credit MUST be checked transactionally by the Study writer first.
    Calling this pure function twice is not proof of two authorized transitions.
    """
    if grade not in ("good", "again"):
        raise ValueError("grade must be good or again")
    if not isinstance(accepted_at, datetime) or accepted_at.utcoffset() is None:
        raise ValueError("accepted_at must be timezone-aware")
    if not isinstance(review_day, date) or isinstance(review_day, datetime):
        raise ValueError("review_day must be an explicit calendar date")
    card = _validated_card(fsrs_card_json)
    accepted_utc = accepted_at.astimezone(timezone.utc)
    if card.last_review is not None and accepted_utc < card.last_review:
        raise ValueError("accepted_at precedes last_review")
    scheduler = Scheduler(desired_retention=DESIRED_RETENTION, learning_steps=(),
        relearning_steps=(), maximum_interval=MAXIMUM_INTERVAL_DAYS, enable_fuzzing=False)
    next_card, _ = scheduler.review_card(
        card, Rating.Good if grade == "good" else Rating.Again, accepted_utc)
    interval = (next_card.due - accepted_utc).days
    return PositionSchedule(
        fsrs_card_json=next_card.to_json(), due_at=next_card.due,
        interval_days=interval, due_date=review_day + timedelta(days=interval),
        stability=float(next_card.stability), internal_rating=grade,
    )
