"""One user-requested practice priority, separate from recall remediation.

The caller owns the transaction, card-before-day locking and current-source
verification. These typed ports contain no SQL, transport or scheduling effects.
Completion means practice served, never recall success or remediation settlement.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Literal, Protocol

REASON = 'historical_exposure'
PRIORITY_LIFETIME = timedelta(days=7)


@dataclass(frozen=True)
class EncounterIdentity:
    authority_id: str
    target_id: str
    workspace_id: str
    repertoire_id: str
    game_id: str
    source_digest: str
    occurred_at: datetime
    ply: int
    card_id: str
    card_revision: int
    executable_digest: str
    route_ids: tuple[str, ...]
    observed_move: str
    expected_moves: tuple[str, ...]

    def key(self) -> str:
        _instant(self.occurred_at)
        values = [getattr(self, name) for name in self.__dataclass_fields__]
        values[6] = self.occurred_at.astimezone(timezone.utc).isoformat()
        return 'historical-exposure:' + hashlib.sha256(json.dumps(values, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class Request:
    encounter: EncounterIdentity
    user_requested: bool
    accepts_unknown_chronology: bool = False


@dataclass(frozen=True)
class CurrentFacts:
    """Adapter-owned facts, never client-supplied eligibility assertions."""
    encounter: EncounterIdentity | None
    mapping: Literal['verified', 'unknown', 'ambiguous']
    membership_ids: tuple[str, ...]
    integrity_current: bool
    card_available: bool
    introduced: bool
    chronology: Literal['introduced_after_game', 'unknown', 'introduced_before_game']
    buried: bool
    protected: bool
    queue_day: str
    queue_entry_id: int | None = None
    priority_present: bool = False


@dataclass(frozen=True)
class Placement:
    status: Literal['applied', 'deferred']
    reason: str | None = None
    queue_entry_id: int | None = None


@dataclass(frozen=True)
class Record:
    request: Request
    accepted_at: datetime
    expires_at: datetime
    state: Literal['active', 'completed', 'expired', 'revoked']
    placement: Placement
    queue_day: str
    completion_review_id: str | None = None

    def priority_active(self, now: datetime) -> bool:
        _instant(now)
        return self.state == 'active' and self.accepted_at <= now < self.expires_at


@dataclass(frozen=True)
class PracticeCompletion:
    card_id: str
    card_revision: int
    queue_entry_id: int
    review_id: str
    reviewed_at: datetime
    source_kind: str
    verified_normal_attempt: bool


class ExposureRejected(ValueError):
    """Semantic refusal, translated by the caller's command adapter."""


class Store(Protocol):
    def find(self, encounter_key: str) -> Record | None: ...
    def save(self, record: Record) -> None: ...
    def another_active(self, card_id: str, excluding: str, now: datetime) -> bool: ...


class QueueWriter(Protocol):
    def prioritize(self, encounter: EncounterIdentity, queue_day: str, reason: str) -> Placement:
        """Canonical protected-head writer; idempotently reuse an existing row."""
        ...

    def clear_priority(self, card_id: str, reason: str) -> None:
        """Clear only this label; never delete/move an attempt or clear a miss."""
        ...


def _instant(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExposureRejected('timezone_required')


def _refusal(request: Request, facts: CurrentFacts, now: datetime) -> str | None:
    identity = request.encounter
    _instant(now)
    _instant(identity.occurred_at)
    if not request.user_requested:
        return 'explicit_user_request_required'
    if (not identity.route_ids or tuple(sorted(set(identity.route_ids))) != identity.route_ids
            or not identity.expected_moves or tuple(sorted(set(identity.expected_moves))) != identity.expected_moves
            or identity.card_revision < 1 or identity.ply < 0 or not identity.card_id
            or identity.observed_move in identity.expected_moves or identity.occurred_at > now):
        return 'verified_departure_required'
    if facts.mapping != 'verified' or facts.encounter != identity:
        return 'current_exact_encounter_required'
    if facts.membership_ids != (identity.repertoire_id,):
        return 'exclusive_current_membership_required'
    if not facts.card_available or not facts.introduced:
        return 'current_introduced_review_card_required'
    if not facts.integrity_current:
        return 'current_integrity_required'
    if facts.chronology == 'introduced_before_game':
        return 'outside_historical_exposure_scope'
    if facts.chronology == 'unknown' and not request.accepts_unknown_chronology:
        return 'chronology_uncertainty_confirmation_required'
    if facts.buried:
        return 'buried_today'
    return None


def _place(request: Request, facts: CurrentFacts, writer: QueueWriter) -> Placement:
    if facts.protected:
        return Placement('deferred', 'protected_attempt_preserved', facts.queue_entry_id)
    return writer.prioritize(request.encounter, facts.queue_day, REASON)


def request_priority(store: Store, writer: QueueWriter, request: Request,
                     facts: CurrentFacts, now: datetime) -> Record:
    """Call under one caller-owned command/receipt transaction; replay is quiet."""
    prior = store.find(request.encounter.key())
    if prior is not None:
        if prior.request != request:
            raise ExposureRejected('exposure_intent_reused')
        return prior  # Do not redraw/extend time, reprioritize or fabricate work.
    refusal = _refusal(request, facts, now)
    if refusal:
        raise ExposureRejected(refusal)
    record = Record(request, now, now + PRIORITY_LIFETIME, 'active',
                    _place(request, facts, writer), facts.queue_day)
    store.save(record)
    return record


def _finish(store: Store, writer: QueueWriter, record: Record, state: str,
            now: datetime, review_id: str | None = None) -> Record:
    result = replace(record, state=state, completion_review_id=review_id)
    store.save(result)
    identity = record.request.encounter
    if not store.another_active(identity.card_id, identity.key(), now):
        writer.clear_priority(identity.card_id, REASON)
    return result


def _current(store: Store, record: Record) -> Record:
    current = store.find(record.request.encounter.key())
    if current is None or current.request != record.request:
        raise ExposureRejected('retained_exposure_required')
    return current


def refresh_priority(store: Store, writer: QueueWriter, record: Record,
                     facts: CurrentFacts, now: datetime) -> Record:
    """Retain finite intent across refresh; never unbury or move protected work."""
    _instant(now)
    record = _current(store, record)
    if record.state != 'active':
        return record
    if now >= record.expires_at:
        return _finish(store, writer, record, 'expired', now)
    if now < record.accepted_at:
        raise ExposureRejected('clock_before_acceptance')
    refusal = _refusal(record.request, facts, now)
    if refusal in {'current_integrity_required', 'buried_today'}:
        result = replace(record, placement=Placement('deferred', refusal))
        store.save(result)
        writer.clear_priority(record.request.encounter.card_id, REASON)
        return result
    if refusal:
        return _finish(store, writer, record, 'revoked', now)
    if (facts.priority_present and facts.queue_entry_id == record.placement.queue_entry_id
            and facts.queue_day == record.queue_day):
        return record
    result = replace(record, placement=_place(record.request, facts, writer), queue_day=facts.queue_day)
    store.save(result)
    return result


def complete_practice(store: Store, writer: QueueWriter, record: Record,
                      completion: PracticeCompletion, now: datetime) -> Record:
    """An actual normal Study completion serves this practice request, any grade.

    Old offline answers, other revisions/attempts, source-game imports and
    fabricated/unverified completions cannot retire priority. No review is made.
    """
    _instant(now)
    _instant(completion.reviewed_at)
    record = _current(store, record)
    if not record.priority_active(now):
        return record
    identity = record.request.encounter
    if (completion.card_id != identity.card_id or completion.card_revision != identity.card_revision
            or completion.queue_entry_id != record.placement.queue_entry_id
            or not completion.review_id or completion.source_kind != 'study'
            or not completion.verified_normal_attempt
            or not record.accepted_at <= completion.reviewed_at <= now):
        return record
    return _finish(store, writer, record, 'completed', now, completion.review_id)
