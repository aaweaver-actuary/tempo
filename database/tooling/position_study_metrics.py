"""Pure bounded aggregation for released position-owned Study receipts.

This module deliberately has no database, filesystem, clock, or service imports.
Callers provide an already bounded page of immutable receipts from the eventual
Study-owned accepted-receipt source.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

Grade = Literal["good", "again", "observe", "pending"]
MAX_ATTEMPT_RECEIPTS = 1_000
MAX_EVENTS_PER_ATTEMPT = 256


@dataclass(frozen=True)
class AcceptedPositionEvent:
    """One Study-owned accepted first-response effect receipt.

    ``event_id`` is the immutable receipt identity. ``study_day`` is the day
    captured by the Study owner, not recomputed in this read-only layer.
    ``observe`` is an actual accepted response that did not create Good/Again
    schedule effect. Pending/skipped positions are represented by no event
    (or grade=pending).
    """

    event_id: str
    repertoire_id: str
    exact_position: str
    grade: Grade
    was_due_at_issue: bool
    accepted_at: datetime | None
    study_day: date | None


@dataclass(frozen=True)
class AttemptReceipt:
    """Immutable accepted attempt summary facts supplied by the owning source."""

    attempt_id: str
    started_at: datetime | None
    completed_at: datetime | None
    events: tuple[AcceptedPositionEvent, ...] = ()
    repeated_moves_avoided: int | None = None
    duplicate_attempts_suppressed: int | None = None
    prefix_holds: int | None = None
    fallback_count: int | None = None
    queue_latency_ms: float | None = None
    review_save_latency_ms: float | None = None
    donor_eligible: int | None = None
    migration_excluded: int | None = None


@dataclass(frozen=True)
class Metrics:
    distinct_positions_tested: int
    actual_response_events: int
    nondue_first_wrong: int
    due_good: int
    due_again: int
    positive_positions_per_day: int | None
    repeated_moves_avoided: int | None
    duplicate_attempts_suppressed: int | None
    prefix_holds: int | None
    fallback_count: int | None
    queue_latency_samples: int
    queue_latency_ms_mean: float | None
    review_save_latency_samples: int
    review_save_latency_ms_mean: float | None
    donor_eligible: int | None
    migration_excluded: int | None
    elapsed_minutes: float | None
    distinct_positions_per_minute: float | None
    retention_7d: None = None
    retention_30d: None = None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def summarize(
    receipts: tuple[AttemptReceipt, ...],
    started_at: datetime | None,
    ended_at: datetime | None,
) -> Metrics:
    """Aggregate a caller-bounded page; duplicate receipt IDs count once.

    Repeated route aliases collapse by (repertoire, exact position). Event IDs
    collapse independently. For throughput, every non-pending event must have
    an aware accepted_at inside the inclusive supplied window; a missing or
    naive event timestamp makes throughput unavailable, and a present event
    outside the window is rejected. Conflicting duplicate IDs are outside the
    released receipt contract and callers must reject them before this call.
    """
    if len(receipts) > MAX_ATTEMPT_RECEIPTS:
        raise ValueError(f"receipt page exceeds {MAX_ATTEMPT_RECEIPTS} attempts")
    if any(len(receipt.events) > MAX_EVENTS_PER_ATTEMPT for receipt in receipts):
        raise ValueError(f"attempt exceeds {MAX_EVENTS_PER_ATTEMPT} position events")

    unique_receipts: dict[str, AttemptReceipt] = {}
    for receipt in receipts:
        unique_receipts.setdefault(receipt.attempt_id, receipt)

    events: dict[str, AcceptedPositionEvent] = {}
    for receipt in unique_receipts.values():
        for event in receipt.events:
            events.setdefault(event.event_id, event)
    actual = [event for event in events.values() if event.grade != "pending"]
    positions = {(event.repertoire_id, event.exact_position) for event in actual}
    wrong = sum(event.grade == "again" and not event.was_due_at_issue for event in actual)
    due_good = sum(event.grade == "good" and event.was_due_at_issue for event in actual)
    due_again = sum(event.grade == "again" and event.was_due_at_issue for event in actual)
    daily_positive: set[tuple[str, str, date]] = set()
    missing_positive_day = False
    for event in actual:
        if event.grade == "good" and event.was_due_at_issue:
            if event.study_day is None:
                missing_positive_day = True
            else:
                daily_positive.add((event.repertoire_id, event.exact_position, event.study_day))

    def sum_optional(name: str) -> int | None:
        vals = [getattr(item, name) for item in unique_receipts.values()]
        return sum(vals) if vals and all(value is not None for value in vals) else None

    elapsed = None
    aware_window = (
        started_at is not None and ended_at is not None
        and started_at.tzinfo is not None and started_at.utcoffset() is not None
        and ended_at.tzinfo is not None and ended_at.utcoffset() is not None
    )
    if aware_window and ended_at > started_at:
        elapsed = (ended_at - started_at).total_seconds() / 60

    all_event_times_valid = bool(aware_window and elapsed is not None)
    if all_event_times_valid:
        for event in actual:
            event_at = event.accepted_at
            if event_at is None or event_at.tzinfo is None or event_at.utcoffset() is None:
                all_event_times_valid = False
                continue
            if event_at < started_at or event_at > ended_at:
                raise ValueError("accepted event falls outside the measurement window")

    return Metrics(
        distinct_positions_tested=len(positions),
        actual_response_events=len(actual),
        nondue_first_wrong=wrong,
        due_good=due_good,
        due_again=due_again,
        positive_positions_per_day=None if missing_positive_day else len(daily_positive),
        repeated_moves_avoided=sum_optional("repeated_moves_avoided"),
        duplicate_attempts_suppressed=sum_optional("duplicate_attempts_suppressed"),
        prefix_holds=sum_optional("prefix_holds"),
        fallback_count=sum_optional("fallback_count"),
        queue_latency_samples=sum(item.queue_latency_ms is not None for item in unique_receipts.values()),
        queue_latency_ms_mean=_mean([item.queue_latency_ms for item in unique_receipts.values() if item.queue_latency_ms is not None]),
        review_save_latency_samples=sum(item.review_save_latency_ms is not None for item in unique_receipts.values()),
        review_save_latency_ms_mean=_mean([item.review_save_latency_ms for item in unique_receipts.values() if item.review_save_latency_ms is not None]),
        donor_eligible=sum_optional("donor_eligible"),
        migration_excluded=sum_optional("migration_excluded"),
        elapsed_minutes=elapsed,
        distinct_positions_per_minute=(
            len(positions) / elapsed
            if all_event_times_valid and elapsed is not None and elapsed > 0
            else None
        ),
    )
