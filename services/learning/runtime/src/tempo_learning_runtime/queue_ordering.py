"""Replayable presentation of admitted work; never selects or admits a card."""

from __future__ import annotations

from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
from typing import NotRequired, Sequence, TypedDict

from .real_game_feedback import MISS_REASON


PRESENTATION_VERSION = "queue-presentation-v3"


class QueueOrderingRow(TypedDict):
    id: int
    card_id: str
    position: int
    content_type: str | None
    admission_kind: str
    gameplay_priority_reason: str | None
    cycle: NotRequired[int]
    attempt_state: NotRequired[str]
    attempt_failed: NotRequired[bool]
    review_completed: NotRequired[bool]
    revision: NotRequired[int]
    issued: NotRequired[bool]
    presented: NotRequired[bool]
    taught: NotRequired[bool]
    eligible: NotRequired[bool]
    frozen_rank: NotRequired[int | None]
    frozen_valid: NotRequired[bool]
    urgent_determined_at: NotRequired[str]
    urgent_stream: NotRequired[str]
    urgent_effect_id: NotRequired[str]


class SavedQueueOrder(TypedDict):
    seed: int
    membership_hash: str


QueueOrderPlan = tuple[int, str, list[QueueOrderingRow]]


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _queue_membership_hash(rows: Sequence[QueueOrderingRow]) -> str:
    """Policy identity fits the existing TEXT fingerprint, without a migration."""
    members = sorted((row["id"], row["card_id"]) for row in rows)
    return PRESENTATION_VERSION + ":" + _digest(members)


def _queue_order_fingerprint(membership_hash: str, frozen_token) -> str:
    if list(frozen_token) == [None, 0]:
        return membership_hash
    return PRESENTATION_VERSION + ":" + _digest([membership_hash, list(frozen_token)])


def _queue_order_provenance(rows, saved, frozen_token):
    fingerprint = _queue_order_fingerprint(_queue_membership_hash(rows), frozen_token)
    if saved and saved["membership_hash"] == fingerprint:
        return {"fingerprint": fingerprint, "frozen_token": list(frozen_token)}
    return None


def _preserved_queue_fingerprint(rows, saved, frozen_token, *, previous=None) -> str:
    fingerprint = _queue_order_fingerprint(_queue_membership_hash(rows), frozen_token)
    if _queue_order_provenance(rows, saved, frozen_token) or (
        previous and saved and saved["membership_hash"] == previous["fingerprint"]
        and previous["frozen_token"] == list(frozen_token)
    ):
        return fingerprint
    # Only a matching pre-mutation receipt can certify a controlled removal or
    # positioned insertion. A v3 prefix alone cannot certify new admissions or
    # a replacement frozen publication that has not yet been presented.
    return "unmixed:" + fingerprint


def _priority_queue_order(rows: Sequence[QueueOrderingRow]) -> list[QueueOrderingRow]:
    """Urgent FIFO precedes unissued ordinary work, retaining genuine attempts."""
    slots = sorted(rows, key=lambda row: (row["position"], row["id"]))
    protected = _protected_queue_ids(rows, urgent=any(_movable_urgent(row) for row in rows))
    movable = [row for row in slots if row["id"] not in protected]
    priority = sorted((row for row in movable if row["gameplay_priority_reason"] == MISS_REASON),
                      key=_urgent_order)
    ordered = iter(priority + [row for row in movable if row["gameplay_priority_reason"] != MISS_REASON])
    return [row if row["id"] in protected else next(ordered) for row in slots]


def _queue_presentation_state(rows: Sequence[QueueOrderingRow]) -> str:
    """Fence a prepared plan against changed positions, eligibility or issuance."""
    fields = ("id", "card_id", "position", "content_type", "admission_kind",
              "gameplay_priority_reason", "cycle", "attempt_state", "attempt_failed",
              "review_completed", "revision", "issued", "presented", "taught", "eligible",
              "urgent_determined_at", "urgent_stream", "urgent_effect_id")
    return _digest([[row.get(field) for field in fields] for row in sorted(rows, key=lambda row: row["id"])])


def _spread_groups(groups, seed, scope):
    events = []
    denominator = 1 << 64
    for key, values in sorted(groups.items()):
        draw = int(_digest([PRESENTATION_VERSION, seed, scope, key, "phase"])[:16], 16)
        phase = denominator // 4 + draw // 2
        tie = _digest([PRESENTATION_VERSION, seed, scope, key, "tie"])
        for ordinal, row in enumerate(values):
            events.append((Fraction(ordinal * denominator + phase, denominator * len(values)), tie, row["id"], row))
    return [event[3] for event in sorted(events)]


def urgent_priority_key(row):
    # The source class and durable determination come from the owning effect.
    # Legacy missing provenance is historical/unknown, never invented live work.
    determined = row.get('urgent_determined_at')
    moment = (datetime.fromisoformat(determined.replace('Z','+00:00')) if determined
              else datetime.min.replace(tzinfo=timezone.utc))
    return (row.get("urgent_stream") != "forward", moment, row.get("urgent_effect_id", ""))


def _urgent_order(row):
    return (*urgent_priority_key(row), row["position"], row["id"])


def _movable_urgent(row):
    return bool(row['gameplay_priority_reason'] == MISS_REASON and row.get('eligible', True)
        and row['admission_kind'] != 'explicit' and not row.get('issued', False)
        and not row.get('presented', False) and not row.get('review_completed', False))


def _protected_queue_ids(rows: Sequence[QueueOrderingRow], *, preserve_through_entry_id=None,
                         urgent=False) -> set[int]:
    """Presentation must not move an active attempt or a positioned repeat."""
    return {row["id"] for row in rows if (
        row.get("eligible", True) is False or row.get("eligible") == 0
        or row["admission_kind"] == "explicit"
        or row.get("issued", False) or row.get("presented", False)
        or row.get("review_completed", False)
        # Historical teaching/repeat/failure flags are not issuance authority.
        # Ordinary mixing still preserves positioned retries, but a ready game
        # obligation can move ahead of every such unissued candidate.
        or (not urgent and (
        (row.get("cycle", 0) > 0 and not (
            row["gameplay_priority_reason"] == MISS_REASON and row.get("attempt_state") == "gameplay"
        ))
        or row.get("attempt_failed", False)
        or row.get("attempt_state", "clean") not in ("clean", "unstarted", "gameplay")
        or (row["content_type"] == "opening" and row["admission_kind"] == "new"
            and (row.get("taught", False) or row.get("frozen_valid", True) is False))
        or (preserve_through_entry_id is not None and row["id"] <= preserve_through_entry_id)
        ))
    )}


def _plan_daily_queue_order(
    rows: Sequence[QueueOrderingRow],
    day: str,
    saved: SavedQueueOrder | None,
    *,
    preserve_through_entry_id: int | None = None,
) -> QueueOrderPlan:
    """Spread each cohort through the movable slots, then retain that order.

    Each group's virtual positions are spaced 1/count apart. Its seeded phase
    lies in the middle half of the first interval; exact fractions avoid clock,
    floating-point and process-randomization dependencies. Priority/explicit and
    protected slots are intentional exceptions to ordinary proportional spacing.
    """
    seed = saved["seed"] if saved else int(hashlib.sha256(day.encode()).hexdigest()[:15], 16)
    slots = sorted(rows, key=lambda row: (row["position"], row["id"]))
    urgent = any(_movable_urgent(row) for row in rows)
    protected = _protected_queue_ids(rows, preserve_through_entry_id=preserve_through_entry_id,
                                     urgent=urgent)
    movable = [row for row in slots if row["id"] not in protected]
    priority = sorted((row for row in movable if row["gameplay_priority_reason"] == MISS_REASON),
                      key=_urgent_order)
    groups: dict[tuple[str, str], list[QueueOrderingRow]] = {}
    for row in movable:
        if row["gameplay_priority_reason"] != MISS_REASON:
            key = (row["admission_kind"], row["content_type"] or "opening")
            groups.setdefault(key, []).append(row)

    cohorts = {}
    for key, values in sorted(groups.items()):
        if key == ("new", "opening"):
            values.sort(key=lambda row: (row.get("frozen_rank") is None,
                        row.get("frozen_rank") or 0, row["position"], row["id"]))
        else:
            values.sort(key=lambda row: (_digest([PRESENTATION_VERSION, seed, key, row["card_id"], row["id"]]), row["id"]))
        cohorts.setdefault(key[0], {})[key[1]] = values
    # Balance review/new cohorts first, then content within each cohort. Equal
    # cohorts still alternate even when one contains more different families.
    cohort_rows = {cohort: _spread_groups(categories, seed, cohort)
                   for cohort, categories in cohorts.items()}
    ordered = iter(priority + _spread_groups(cohort_rows, seed, "cohorts"))
    result = [row if row["id"] in protected else next(ordered) for row in slots]
    return seed, _queue_membership_hash(rows), result
