"""Small within-session retry decisions over caller-owned durable facts.

Admission, clocks, random draws, SQL, locks and transport are supplied by the
composition. The saved decision is immutable; serving never draws again.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import TYPE_CHECKING, Literal

from .models import QueueAttempt

if TYPE_CHECKING:
    from .ports import QueueSelectionStore

RETRY_POLICY_VERSION = "soft-failed-retry-v1"


@dataclass(frozen=True)
class FailedRetryRequest:
    review_id: int
    source_attempt: QueueAttempt
    reviewed_at: datetime


@dataclass(frozen=True)
class RetryPlan:
    policy_version: str
    delay_seconds: int
    ready_at: datetime
    order_token: str


@dataclass(frozen=True)
class QueueIdentity:
    entry_id: int
    card_id: str
    revision: int
    cycle: int
    local_date: str
    issued: bool = False


@dataclass(frozen=True)
class RetryCandidate:
    identity: QueueIdentity
    ready_at: datetime
    order_token: str
    urgent: bool = False
    released_at: datetime | None = None


@dataclass(frozen=True)
class QueueReadyFacts:
    """Whole-queue facts supplied by the authority, never a page projection.

    A pending essential admission/projection or contended ready candidate cannot
    be represented as complete absence. A selected due/cooling candidate is the
    first under the persisted priority/token order, not a freshly sampled one.
    """
    ready_head: QueueIdentity | None
    due_retry: RetryCandidate | None
    cooling_retry: RetryCandidate | None
    complete: bool
    pending_reason: Literal["essential_queue_pending", "issued_retry_origin_missing"] | None = None
    missing_issued: QueueIdentity | None = None


@dataclass(frozen=True)
class QueueAction:
    kind: Literal["issue", "activate", "early_retry", "recover", "pending", "exhausted"]
    identity: QueueIdentity | None = None
    reason: Literal["deadline", "no_ready_work", "essential_queue_pending", "issued_retry_origin_missing"] | None = None


@dataclass(frozen=True)
class QueueContext:
    """Explicit instant and the existing admission owner's authoritative day."""
    observed_at: datetime
    local_date: str


@dataclass(frozen=True)
class QueueSelection:
    status: Literal["issued", "changed", "pending", "conflict", "exhausted"]
    identity: QueueIdentity | None = None
    reason: str | None = None


def select_queue_head(store: "QueueSelectionStore", context: QueueContext) -> QueueSelection:
    """Propose from facts; the adapter rechecks under its borrowed transaction."""
    action = choose_queue_action(store.queue_ready_facts(context), observed_at=context.observed_at)
    return store.apply_queue_action(action, context)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Retry times must include a timezone")
    return value.astimezone(timezone.utc)


def _token(value: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("Retry order token must be 32 lowercase hexadecimal characters")
    return int(value, 16)


def plan_failed_retry(request: FailedRetryRequest, *, delay_seconds: int,
                      order_token: str) -> RetryPlan:
    if type(delay_seconds) is not int or not 300 <= delay_seconds <= 600:
        raise ValueError("Failed retry delay must be an integer from 300 to 600 seconds")
    if type(request.review_id) is not int or request.review_id <= 0:
        raise ValueError("Failed retry requires a completed review identity")
    _token(order_token)
    return RetryPlan(RETRY_POLICY_VERSION, delay_seconds,
                     _utc(request.reviewed_at) + timedelta(seconds=delay_seconds), order_token)


def retry_order_key(candidate: RetryCandidate) -> tuple[bool, str, int]:
    _token(candidate.order_token)
    return (not candidate.urgent, candidate.order_token, candidate.identity.entry_id)


def choose_queue_action(facts: QueueReadyFacts, *, observed_at: datetime) -> QueueAction:
    now = _utc(observed_at)
    if facts.ready_head and facts.ready_head.issued:
        return QueueAction("issue", facts.ready_head)
    if facts.missing_issued:
        return QueueAction('recover',facts.missing_issued,'issued_retry_origin_missing')
    candidate = facts.due_retry
    if candidate:
        if candidate.released_at is None and _utc(candidate.ready_at) > now:
            raise ValueError("Due retry facts precede their natural deadline")
        return QueueAction("activate", candidate.identity,
                           "deadline" if candidate.released_at is None else None)
    if facts.ready_head:
        return QueueAction("issue", facts.ready_head)
    if not facts.complete:
        return QueueAction("pending", reason=facts.pending_reason or "essential_queue_pending")
    if facts.cooling_retry:
        candidate = facts.cooling_retry
        if _utc(candidate.ready_at) <= now:
            return QueueAction("activate", candidate.identity, "deadline")
        return QueueAction("early_retry", candidate.identity, "no_ready_work")
    return QueueAction("exhausted")


def retry_insertion_order(rows: tuple[tuple[int, bool], ...], *,
                          retry_entry_id: int, order_token: str) -> tuple[int, ...]:
    """Permute supplied legal slots only; caller excludes protected attempts.

    Existing peers retain relative order in their priority tier. Persist this
    resulting placement once when the saved decision is activated.
    """
    draw = _token(order_token)
    identities = [entry for entry, _urgent in rows]
    if len(set(identities)) != len(identities) or retry_entry_id not in identities:
        raise ValueError("Retry insertion requires one matching identity")
    urgent = next(priority for entry, priority in rows if entry == retry_entry_id)
    peers = [entry for entry, priority in rows if priority == urgent and entry != retry_entry_id]
    peers.insert(draw % (len(peers) + 1), retry_entry_id)
    other = [entry for entry, priority in rows if priority != urgent]
    return tuple(peers + other if urgent else other + peers)
