"""One user-requested practice priority, separate from recall remediation.

The caller owns the transaction, card-before-day locking and current-source
verification. These typed ports contain no SQL, transport or scheduling effects.
Completion means practice served, never recall success or remediation settlement.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from typing import Literal, Protocol

REASON = 'historical_exposure'


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
    priority_class: Literal['none', 'exposure', 'recall', 'other'] = 'none'


@dataclass(frozen=True)
class Placement:
    status: Literal['applied', 'deferred']
    reason: str | None = None
    queue_entry_id: int | None = None


@dataclass(frozen=True)
class Record:
    request: Request
    accepted_at: datetime
    state: Literal['active', 'completed', 'cancelled', 'revoked', 'superseded']
    placement: Placement
    queue_day: str
    completion_review_id: str | None = None
    terminal_reason: str | None = None

    def intent_active(self, now: datetime) -> bool:
        _instant(now)
        return self.state == 'active' and self.accepted_at <= now

    def card_scope(self) -> 'CardScope':
        identity = self.request.encounter
        return CardScope(identity.target_id, identity.workspace_id, identity.card_id)


@dataclass(frozen=True)
class PracticeCompletion:
    card_id: str
    card_revision: int
    queue_entry_id: int
    review_id: str
    reviewed_at: datetime
    source_kind: str
    verified_normal_attempt: bool
    scope: 'CardScope'


class ExposureRejected(ValueError):
    """Semantic refusal, translated by the caller's command adapter."""


@dataclass(frozen=True)
class CardScope:
    target_id: str
    workspace_id: str
    card_id: str


class FactReader(Protocol):
    def current(self, request: Request) -> CurrentFacts:
        """Fresh verified facts from the caller's locked, consistent card scope."""
        ...


class Store(Protocol):
    def find(self, encounter_key: str) -> Record | None: ...
    def save(self, record: Record) -> None: ...
    def active_for_card(self, scope: CardScope) -> tuple[Record, ...]:
        """Complete active inventory; no silent truncation or other workspace."""
        ...


class QueueWriter(Protocol):
    def prioritize(self, encounter: EncounterIdentity, queue_day: str, reason: str) -> Placement:
        """Canonical protected-head writer; idempotently reuse an existing row."""
        ...

    def clear_priority(self, scope: CardScope, reason: str) -> None:
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



def _current(store: Store, record: Record) -> Record:
    current = store.find(record.request.encounter.key())
    if current is None or current.request != record.request:
        raise ExposureRejected('retained_exposure_required')
    return current


def reconcile_card_priority(store: Store, writer: QueueWriter, reader: FactReader,
                            scope: CardScope, now: datetime) -> None:
    """One eligible-contributor rule for refresh, completion and cancellation.

    The caller must serialize the entire target/workspace/card scope, including
    its complete active inventory and current facts, in the same transaction as
    the canonical protected-head writer. Leases may expire; training intent does
    not. Read/decide before effects. No individual request clears a card's label.
    """
    _instant(now)
    records = store.active_for_card(scope)
    keys = [record.request.encounter.key() for record in records]
    if len(set(keys)) != len(keys) or any(record.card_scope()!=scope or record.state!='active' for record in records):
        raise ExposureRejected('complete_scoped_active_inventory_required')
    updates, eligible = [], []
    for record in records:
        if now < record.accepted_at:
            raise ExposureRejected('clock_before_acceptance')
        facts = reader.current(record.request)
        refusal = _refusal(record.request, facts, now)
        if refusal is None:
            eligible.append((record, facts))
        elif facts.mapping == 'verified' and facts.encounter != record.request.encounter:
            updates.append(replace(record, state='superseded', terminal_reason='verified_source_changed'))
        elif refusal in {'exclusive_current_membership_required', 'outside_historical_exposure_scope'}:
            updates.append(replace(record, state='revoked', terminal_reason=refusal))
        else:
            # Unknown mapping, burial, unavailable cards or stale integrity are
            # not a reason to silently discard unserved user intent.
            updates.append(replace(record, placement=Placement('deferred', refusal, record.placement.queue_entry_id)))
    if eligible:
        queue_facts = {(facts.queue_day, facts.queue_entry_id, facts.protected, facts.priority_class)
                       for _,facts in eligible}
        if len(queue_facts) != 1:
            raise ExposureRejected('consistent_card_queue_facts_required')
    for updated in updates:
        if updated != store.find(updated.request.encounter.key()):
            store.save(updated)
    if not eligible:
        writer.clear_priority(scope, REASON)
        return
    selected, facts = min(eligible, key=lambda item: item[0].request.encounter.key())
    if facts.priority_class in {'recall','other'}:
        placement = Placement('deferred', 'unrelated_priority_preserved', facts.queue_entry_id)
    elif facts.protected:
        placement = Placement('deferred', 'protected_attempt_preserved', facts.queue_entry_id)
    elif facts.priority_class == 'exposure' and facts.queue_entry_id is not None:
        placement = Placement('applied', queue_entry_id=facts.queue_entry_id)
    else:
        placement = writer.prioritize(selected.request.encounter, facts.queue_day, REASON)
    for record,_ in eligible:
        updated = replace(record, placement=placement, queue_day=facts.queue_day)
        if updated != record:
            store.save(updated)


def request_priority(store: Store, writer: QueueWriter, request: Request,
                     reader: FactReader, now: datetime) -> Record:
    """One caller-owned command/receipt transaction; duplicate intent is quiet."""
    prior = store.find(request.encounter.key())
    if prior is not None:
        if prior.request != request:
            raise ExposureRejected('exposure_intent_reused')
        return prior
    facts = reader.current(request)
    refusal = _refusal(request, facts, now)
    if refusal:
        raise ExposureRejected(refusal)
    record = Record(request, now, 'active', Placement('deferred','pending_reconciliation'), facts.queue_day)
    store.save(record)
    reconcile_card_priority(store, writer, reader, record.card_scope(), now)
    return _current(store, record)


def refresh_priority(store: Store, writer: QueueWriter, record: Record,
                     reader: FactReader, now: datetime) -> Record:
    record = _current(store, record)
    if record.state == 'active':
        reconcile_card_priority(store, writer, reader, record.card_scope(), now)
    return _current(store, record)


def _finish(store: Store, writer: QueueWriter, reader: FactReader, record: Record,
            state: Literal['completed', 'cancelled'], reason: str, now: datetime, review_id: str | None = None) -> Record:
    result = replace(record, state=state, terminal_reason=reason, completion_review_id=review_id)
    store.save(result)
    reconcile_card_priority(store, writer, reader, record.card_scope(), now)
    return result


def cancel_priority(store: Store, writer: QueueWriter, record: Record,
                    reader: FactReader, now: datetime) -> Record:
    """Explicit cancellation by the authenticated request owner; never age-out."""
    _instant(now)
    record = _current(store, record)
    if not record.intent_active(now):
        return record
    return _finish(store, writer, reader, record, 'cancelled','user_cancelled',now)


def complete_practice(store: Store, writer: QueueWriter, record: Record,
                      completion: PracticeCompletion, reader: FactReader, now: datetime) -> Record:
    """Actual normal Study practice serves the request, any grade, not mastery.

    Old offline answers, other revisions/attempts, game imports and unverified
    completions cannot retire intent. No review or remediation settlement is made.
    """
    _instant(now)
    _instant(completion.reviewed_at)
    record = _current(store, record)
    if not record.intent_active(now):
        return record
    identity = record.request.encounter
    if (completion.scope != record.card_scope()
            or completion.card_id != identity.card_id or completion.card_revision != identity.card_revision
            or completion.queue_entry_id != record.placement.queue_entry_id
            or not completion.review_id or completion.source_kind != 'study'
            or not completion.verified_normal_attempt
            or not record.accepted_at <= completion.reviewed_at <= now):
        return record
    return _finish(store, writer, reader, record, 'completed','practice_served',now,completion.review_id)
