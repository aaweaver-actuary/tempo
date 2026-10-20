"""Semantic fakes: no persistence, queue SQL, scheduling or service runtime."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from tempo_learning_review import historical_exposure as exposure

NOW = datetime(2026,10,20,12,tzinfo=timezone.utc)


def encounter():
    return exposure.EncounterIdentity('authority','target','legacy','repertoire','game','source',
        datetime(2026,10,9,tzinfo=timezone.utc),8,'physical',1,'executable',('route-a','route-b'),
        'g1f3',('d1f3',))


def facts(**changes):
    return replace(exposure.CurrentFacts(encounter(),'verified',('repertoire',),True,True,True,
        'introduced_after_game',False,False,'2026-10-20'),**changes)


def scope(request):
    identity=request.encounter
    return exposure.CardScope(identity.target_id,identity.workspace_id,identity.card_id)


class Reader:
    queue_fields=('queue_day','queue_entry_id','protected','priority_class')
    def __init__(self,request,current):
        self.sources,self.queue={},{ }
        self.add(request,current)
    def add(self,request,current):
        self.sources[request.encounter.key()]=current
        self.queue.setdefault(scope(request),{name:getattr(current,name) for name in self.queue_fields})
    def current(self,request):
        return replace(self.sources[request.encounter.key()],**self.queue[scope(request)])
    def change(self,request,**changes):
        self.queue[scope(request)].update({key:value for key,value in changes.items() if key in self.queue_fields})
        self.sources[request.encounter.key()]=replace(self.sources[request.encounter.key()],
            **{key:value for key,value in changes.items() if key not in self.queue_fields})
        return self


class Store:
    def __init__(self): self.rows,self.saves={},[]
    def find(self,key): return self.rows.get(key)
    def save(self,record):
        self.rows[record.request.encounter.key()]=record
        self.saves.append(record)
    def active_for_card(self,card_scope):
        return tuple(record for record in self.rows.values()
                     if record.card_scope()==card_scope and record.state=='active')


class Writer:
    def __init__(self,reader): self.reader,self.calls,self.cleared=reader,[],[]
    def prioritize(self,identity,queue_day,reason):
        card_scope=exposure.CardScope(identity.target_id,identity.workspace_id,identity.card_id)
        assert self.reader.queue[card_scope]['priority_class'] not in {'recall','other'}
        self.calls.append((identity,queue_day,reason))
        self.reader.queue[card_scope].update(queue_day=queue_day,queue_entry_id=42,priority_class='exposure')
        return exposure.Placement('applied',queue_entry_id=42)
    def clear_priority(self,card_scope,reason):
        self.cleared.append((card_scope,reason))
        assert reason=='historical_exposure'
        if self.reader.queue[card_scope]['priority_class']=='exposure':
            self.reader.queue[card_scope]['priority_class']='none'


def accepted(**changes):
    request=exposure.Request(encounter(),True)
    reader=Reader(request,facts(**changes))
    store,writer=Store(),Writer(reader)
    record=exposure.request_priority(store,writer,request,reader,NOW)
    return store,writer,record


def paired():
    store,writer,record=accepted()
    other=replace(record.request,encounter=replace(encounter(),game_id='second-game'))
    writer.reader.add(other,facts(encounter=other.encounter))
    second=exposure.request_priority(store,writer,other,writer.reader,NOW)
    return store,writer,record,second


def changed(writer,record=None,**changes):
    return writer.reader.change(record.request if record else exposure.Request(encounter(),True),**changes)


def test_explicit_historical_departure_prioritizes_practice_without_miss_or_review():
    store,writer,record=accepted()
    assert record.state=='active' and not hasattr(record,'expires_at')
    assert writer.calls==[(encounter(),'2026-10-20','historical_exposure')]
    assert record.completion_review_id is None and len(store.rows)==1 and not writer.cleared


@pytest.mark.parametrize('change,reason',[
    ({'mapping':'unknown'},'current_exact_encounter_required'),
    ({'mapping':'ambiguous'},'current_exact_encounter_required'),
    ({'encounter':replace(encounter(),card_revision=2)},'current_exact_encounter_required'),
    ({'encounter':replace(encounter(),source_digest='changed')},'current_exact_encounter_required'),
    ({'encounter':replace(encounter(),route_ids=('other-route',))},'current_exact_encounter_required'),
    ({'membership_ids':('other',)},'exclusive_current_membership_required'),
    ({'membership_ids':('repertoire','other')},'exclusive_current_membership_required'),
    ({'card_available':False},'current_introduced_review_card_required'),
    ({'introduced':False},'current_introduced_review_card_required'),
    ({'integrity_current':False},'current_integrity_required'),
    ({'buried':True},'buried_today'),
    ({'chronology':'introduced_before_game'},'outside_historical_exposure_scope'),
    ({'chronology':'unknown'},'chronology_uncertainty_confirmation_required'),
])
def test_unknown_stale_buried_or_ineligible_source_cannot_admit(change,reason):
    request=exposure.Request(encounter(),True)
    reader=Reader(request,facts(**change));store,writer=Store(),Writer(reader)
    with pytest.raises(exposure.ExposureRejected,match=reason):
        exposure.request_priority(store,writer,request,reader,NOW)
    assert not store.saves and not writer.calls


def test_explicit_user_intent_and_chronology_uncertainty_are_separate():
    request=exposure.Request(encounter(),False)
    reader=Reader(request,facts());store,writer=Store(),Writer(reader)
    with pytest.raises(exposure.ExposureRejected,match='explicit_user_request_required'):
        exposure.request_priority(store,writer,request,reader,NOW)
    request=exposure.Request(encounter(),True,accepts_unknown_chronology=True)
    reader.change(request,chronology='unknown')
    assert exposure.request_priority(store,writer,request,reader,NOW).state=='active'


def test_protected_target_is_retained_without_moving_or_issuing_any_attempt():
    _,writer,record=accepted(protected=True,queue_entry_id=7)
    assert record.placement==exposure.Placement('deferred','protected_attempt_preserved',7)
    assert not writer.calls and not writer.cleared


def test_response_loss_replay_never_creates_placement_or_extends_intent():
    store,writer,record=accepted();before=len(store.saves)
    assert exposure.request_priority(store,writer,record.request,writer.reader,NOW+timedelta(days=80))==record
    assert len(store.saves)==before and len(writer.calls)==1
    assert record.intent_active(NOW+timedelta(days=80))


def test_reused_encounter_cannot_change_confirmation_or_revive_intent():
    store,writer,record=accepted()
    with pytest.raises(exposure.ExposureRejected,match='exposure_intent_reused'):
        exposure.request_priority(store,writer,replace(record.request,accepts_unknown_chronology=True),writer.reader,NOW)
    assert len(writer.calls)==1


def test_refresh_reuses_current_priority_and_retains_same_intent_next_day():
    store,writer,record=accepted()
    assert exposure.refresh_priority(store,writer,record,writer.reader,NOW)==record
    assert len(writer.calls)==1
    updated=exposure.refresh_priority(store,writer,record,changed(writer,queue_day='2026-10-21',
        queue_entry_id=None,priority_class='none'),NOW+timedelta(days=1))
    assert len(writer.calls)==2 and updated.accepted_at==record.accepted_at


@pytest.mark.parametrize('change',[{'buried':True},{'integrity_current':False},{'mapping':'unknown'},
    {'mapping':'ambiguous'},{'card_available':False}])
def test_temporary_ineligibility_retains_intent_but_cannot_contribute_priority(change):
    store,writer,record=accepted()
    updated=exposure.refresh_priority(store,writer,record,changed(writer,**change),NOW)
    assert updated.state=='active' and updated.placement.status=='deferred'
    assert len(writer.calls)==1 and writer.cleared==[(record.card_scope(),'historical_exposure')]


def test_only_verified_source_supersession_or_revocation_ends_unserved_intent():
    for current,state in [(dict(encounter=replace(encounter(),card_revision=2)),'superseded'),
            (dict(membership_ids=('other',)),'revoked')]:
        store,writer,record=accepted()
        ended=exposure.refresh_priority(store,writer,record,changed(writer,**current),NOW)
        assert ended.state==state and ended.completion_review_id is None
        assert writer.cleared==[(record.card_scope(),'historical_exposure')]
        assert exposure.refresh_priority(store,writer,ended,writer.reader,NOW)==ended
        assert len(writer.calls)==1


def completion(**changes):
    return replace(exposure.PracticeCompletion('physical',1,42,'review-1',NOW,'study',True,
        exposure.CardScope('target','legacy','physical')),**changes)


@pytest.mark.parametrize('change',[{'card_revision':2},{'queue_entry_id':99},{'source_kind':'gameplay'},
    {'verified_normal_attempt':False},{'reviewed_at':NOW-timedelta(seconds=1)},
    {'reviewed_at':NOW+timedelta(seconds=1)},{'review_id':''}])
def test_only_actual_subsequent_bound_study_practice_can_complete_priority(change):
    store,writer,record=accepted();before=len(store.saves)
    assert exposure.complete_practice(store,writer,record,completion(**change),writer.reader,NOW)==record
    assert not writer.cleared and len(store.saves)==before


def test_normal_practice_completion_is_exactly_once_not_mastery_or_remediation_settlement():
    store,writer,record=accepted();before=len(store.saves)
    done=exposure.complete_practice(store,writer,record,completion(),writer.reader,NOW)
    assert done.state=='completed' and done.terminal_reason=='practice_served' and done.completion_review_id=='review-1'
    assert not done.intent_active(NOW)
    assert exposure.complete_practice(store,writer,record,completion(),writer.reader,NOW)==done
    assert len(store.saves)==before+1 and writer.cleared==[(record.card_scope(),'historical_exposure')]


@pytest.mark.parametrize('other_scope',[
    exposure.CardScope('other-target','legacy','physical'),
    exposure.CardScope('target','other-workspace','physical'),
])
def test_completion_with_identical_local_ids_in_another_scope_cannot_retire_intent(other_scope):
    store,writer,record=accepted()
    before=dict(store.rows),len(store.saves),list(writer.calls)
    # Every local identifier, revision, timestamp and verification flag is the
    # same. Only the target/workspace authority differs.
    event=completion(scope=other_scope)
    assert event.card_id==record.request.encounter.card_id and event.queue_entry_id==42
    assert event.review_id=='review-1' and event.verified_normal_attempt
    assert exposure.complete_practice(store,writer,record,event,writer.reader,NOW)==record
    assert (store.rows,len(store.saves),writer.calls)==before
    assert not writer.cleared


def test_same_game_instant_in_another_offset_has_same_idempotent_identity():
    shifted=replace(encounter(),occurred_at=encounter().occurred_at.astimezone(timezone(timedelta(hours=-4))))
    assert shifted.key()==encounter().key()
    with pytest.raises(exposure.ExposureRejected,match='timezone_required'):
        replace(encounter(),occurred_at=datetime(2026,10,9)).key()


def test_unserved_user_priority_survives_more_than_seven_days():
    store,writer,record=accepted();later=NOW+timedelta(days=30)
    refreshed=exposure.refresh_priority(store,writer,record,writer.reader,later)
    assert refreshed.state=='active' and refreshed.intent_active(later) and not writer.cleared


def test_refresh_stale_integrity_request_does_not_erase_valid_paired_contribution():
    store,writer,record,second=paired()
    refreshed=exposure.refresh_priority(store,writer,record,changed(writer,integrity_current=False),NOW)
    assert refreshed.state=='active' and refreshed.placement.status=='deferred'
    assert store.find(second.request.encounter.key()).placement.status=='applied'
    assert not writer.cleared and len(writer.calls)==1


@pytest.mark.parametrize('operation',['completion','cancellation'])
@pytest.mark.parametrize('other_eligible',[False,True])
def test_card_priority_after_completion_or_cancellation_depends_on_eligible_contributors(operation,other_eligible):
    store,writer,record,second=paired()
    if not other_eligible: changed(writer,second,integrity_current=False)
    if operation=='completion':
        ended=exposure.complete_practice(store,writer,record,completion(),writer.reader,NOW)
        assert ended.state=='completed'
    else:
        ended=exposure.cancel_priority(store,writer,record,writer.reader,NOW)
        assert ended.state=='cancelled' and ended.terminal_reason=='user_cancelled'
        assert exposure.cancel_priority(store,writer,record,writer.reader,NOW)==ended
    assert bool(writer.cleared) is not other_eligible
    assert store.find(second.request.encounter.key()).state=='active'
    assert store.find(second.request.encounter.key()).placement.status==('applied' if other_eligible else 'deferred')


def test_one_refresh_cannot_preserve_label_using_another_ineligible_active_intent():
    store,writer,record,second=paired()
    changed(writer,second,mapping='unknown')
    updated=exposure.refresh_priority(store,writer,record,changed(writer,integrity_current=False),NOW)
    assert updated.state=='active' and store.find(second.request.encounter.key()).state=='active'
    assert writer.cleared==[(record.card_scope(),'historical_exposure')]


@pytest.mark.parametrize('priority_class',['recall','other'])
def test_unrelated_priority_is_never_overwritten_or_cleared(priority_class):
    store,writer,record=accepted(priority_class=priority_class,queue_entry_id=7)
    assert record.placement==exposure.Placement('deferred','unrelated_priority_preserved',7)
    assert not writer.calls and writer.reader.queue[record.card_scope()]['priority_class']==priority_class
    exposure.cancel_priority(store,writer,record,writer.reader,NOW)
    assert writer.reader.queue[record.card_scope()]['priority_class']==priority_class


def test_reconciliation_is_target_and_workspace_scoped():
    store,writer,record,second=paired()
    other_request=replace(record.request,encounter=replace(encounter(),workspace_id='other'))
    writer.reader.add(other_request,facts(encounter=other_request.encounter))
    other=exposure.request_priority(store,writer,other_request,writer.reader,NOW)
    exposure.cancel_priority(store,writer,record,writer.reader,NOW)
    exposure.cancel_priority(store,writer,second,writer.reader,NOW)
    assert writer.cleared==[(record.card_scope(),'historical_exposure')]
    assert store.find(other_request.encounter.key()).state=='active'
    assert writer.reader.queue[other.card_scope()]['priority_class']=='exposure'
