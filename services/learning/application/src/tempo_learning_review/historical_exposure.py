"""Durable request to practice one verified encountered card through normal Study.

Ordinary queue admission is not fulfillment. No priority overlay, attempt ledger,
review creation, scheduler, connection or remediation settlement lives here.
"""
from dataclasses import dataclass,replace
from datetime import datetime,timezone
import hashlib
import json
from typing import Literal,Protocol


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
    route_ids: tuple[str,...]
    observed_move: str
    expected_moves: tuple[str,...]

    def key(self):
        _instant(self.occurred_at)
        values=[getattr(self,name) for name in self.__dataclass_fields__]
        values[6]=self.occurred_at.astimezone(timezone.utc).isoformat()
        return 'historical-exposure:'+hashlib.sha256(json.dumps(values,separators=(',',':')).encode()).hexdigest()


@dataclass(frozen=True)
class Request:
    encounter: EncounterIdentity
    user_requested: bool
    accepts_unknown_chronology: bool=False


@dataclass(frozen=True)
class CardScope:
    target_id: str
    workspace_id: str
    card_id: str


@dataclass(frozen=True)
class CurrentFacts:
    encounter: EncounterIdentity | None
    mapping: Literal['verified','unknown','ambiguous']
    membership_ids: tuple[str,...]
    integrity_current: bool
    card_available: bool
    introduced: bool
    chronology: Literal['introduced_after_game','unknown','introduced_before_game']
    buried: bool
    queue_day: str
    current_revision: int | None=None
    queue_entry_id: int | None=None


@dataclass(frozen=True)
class Placement:
    status: Literal['queued','deferred']
    reason: str | None=None
    queue_entry_id: int | None=None
    mode: Literal['ordinary_queue','best_effort_front']='ordinary_queue'


@dataclass(frozen=True)
class Record:
    request: Request
    accepted_at: datetime
    state: Literal['pending','served','cancelled','revoked','superseded']
    placement: Placement
    queue_day: str
    completion_review_id: str | None=None
    terminal_reason: str | None=None
    positioned_entry_id: int | None=None

    def card_scope(self):
        value=self.request.encounter
        return CardScope(value.target_id,value.workspace_id,value.card_id)


@dataclass(frozen=True)
class NormalPractice:
    scope: CardScope
    card_revision: int
    queue_entry_id: int
    review_id: str
    issued_at: datetime
    reviewed_at: datetime


class ExposureRejected(ValueError):pass


class Store(Protocol):
    def find(self,key: str) -> Record | None: ...
    def save(self,record: Record) -> None: ...
    def pending_for_card(self,scope: CardScope) -> tuple[Record,...]: ...


class FactReader(Protocol):
    def current(self,request: Request) -> CurrentFacts: ...


class QueueWriter(Protocol):
    def admit(self,encounter: EncounterIdentity,queue_day: str,*,prefer_front: bool) -> Placement:
        """Canonical admission with one-time best effort; preserve the active head."""
        ...


def _instant(value):
    if value.tzinfo is None or value.utcoffset() is None:raise ExposureRejected('timezone_required')


def refusal(request,facts,now):
    identity=request.encounter;_instant(now);_instant(identity.occurred_at)
    if not request.user_requested:return 'explicit_user_request_required'
    if (not identity.route_ids or tuple(sorted(set(identity.route_ids)))!=identity.route_ids
        or not identity.expected_moves or tuple(sorted(set(identity.expected_moves)))!=identity.expected_moves
        or identity.card_revision<1 or identity.ply<0 or identity.observed_move in identity.expected_moves
        or identity.occurred_at>now):return 'verified_departure_required'
    if facts.mapping!='verified' or facts.encounter!=identity:return 'current_exact_encounter_required'
    if facts.membership_ids!=(identity.repertoire_id,):return 'exclusive_current_membership_required'
    if not facts.card_available or not facts.introduced:return 'current_introduced_review_card_required'
    if not facts.integrity_current:return 'current_integrity_required'
    if facts.chronology=='introduced_before_game':return 'outside_historical_exposure_scope'
    if facts.chronology=='unknown' and not request.accepts_unknown_chronology:return 'chronology_uncertainty_confirmation_required'
    return None


def practice_serves(record,event,now):
    _instant(now);_instant(event.issued_at);_instant(event.reviewed_at)
    return (event.scope==record.card_scope() and event.card_revision==record.request.encounter.card_revision
        and event.queue_entry_id>0 and bool(event.review_id)
        and record.accepted_at<=event.issued_at<=event.reviewed_at<=now)


def process_card(store,writer,reader,scope,practices,now):
    """Complete verified practice first, then admit remaining eligible requests once."""
    _instant(now);records=store.pending_for_card(scope)
    keys=[row.request.encounter.key() for row in records]
    if len(keys)!=len(set(keys)) or any(row.card_scope()!=scope or row.state!='pending' for row in records):
        raise ExposureRejected('complete_scoped_pending_inventory_required')
    eligible=[]
    for record in records:
        if now<record.accepted_at:raise ExposureRejected('clock_before_acceptance')
        matches=[event for event in practices if practice_serves(record,event,now)]
        if matches:
            event=min(matches,key=lambda value:(value.reviewed_at,value.review_id))
            store.save(replace(record,state='served',completion_review_id=event.review_id,terminal_reason='normal_practice'))
            continue
        facts=reader.current(record.request)
        if facts.current_revision is not None and facts.current_revision!=record.request.encounter.card_revision:
            store.save(replace(record,state='superseded',terminal_reason='card_revision_changed'));continue
        reason=refusal(record.request,facts,now)
        if reason or facts.buried:
            store.save(replace(record,placement=Placement('deferred',reason or 'buried_today'),queue_day=facts.queue_day));continue
        eligible.append((record,facts))
    if eligible:
        if len({facts.queue_day for _,facts in eligible})!=1:raise ExposureRejected('consistent_queue_day_required')
        selected,facts=eligible[0]
        placement=writer.admit(selected.request.encounter,facts.queue_day,
            prefer_front=facts.queue_entry_id is None or any(
                record.positioned_entry_id!=facts.queue_entry_id for record,_ in eligible))
        for record,_ in eligible:
            updated=replace(record,placement=placement,queue_day=facts.queue_day,
                positioned_entry_id=placement.queue_entry_id if placement.status=='queued' else record.positioned_entry_id)
            if updated!=record:store.save(updated)
    return tuple(store.find(record.request.encounter.key()) for record in records)


def request_practice(store,writer,request,reader,now):
    prior=store.find(request.encounter.key())
    if prior is not None:
        if prior.request!=request:raise ExposureRejected('exposure_intent_reused')
        return prior
    facts=reader.current(request);reason=refusal(request,facts,now)
    if reason:raise ExposureRejected(reason)
    record=Record(request,now,'pending',Placement('deferred','awaiting_ordinary_queue'),facts.queue_day)
    store.save(record)
    process_card(store,writer,reader,record.card_scope(),(),now)
    return store.find(request.encounter.key())


def cancel_request(store,record,now):
    """Cancel retry intent only; never admit, move or delete an ordinary attempt."""
    _instant(now);current=store.find(record.request.encounter.key())
    if current is None or current.request!=record.request:raise ExposureRejected('retained_exposure_required')
    if current.state=='pending':
        current=replace(current,state='cancelled',terminal_reason='user_cancelled');store.save(current)
    return current
