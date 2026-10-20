"""MVP practice-request policy through semantic fake capabilities."""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import pytest
from tempo_learning_review import historical_exposure as practice

NOW=datetime(2026,10,20,12,tzinfo=timezone.utc)


def identity():
    return practice.EncounterIdentity('authority','target','legacy','repertoire','game','source',
        NOW-timedelta(days=10),8,'physical',1,'executable',('route',),'g1f3',('d1f3',))


def facts(**changes):
    return replace(practice.CurrentFacts(identity(),'verified',('repertoire',),True,True,True,
        'introduced_after_game',False,'2026-10-20',1),**changes)


class Store:
    def __init__(self):self.rows={}
    def find(self,key):return self.rows.get(key)
    def save(self,record):self.rows[record.request.encounter.key()]=record
    def pending_for_card(self,scope):return tuple(row for row in self.rows.values() if row.card_scope()==scope and row.state=='pending')


class Reader:
    def __init__(self,current):self.facts=current
    def current(self,request):return replace(self.facts,encounter=request.encounter) if self.facts.mapping=='verified' else self.facts


class Writer:
    def __init__(self):self.calls=[]
    def admit(self,encounter,day):self.calls.append((encounter,day));return practice.Placement('queued',queue_entry_id=42)


def accepted(current=None):
    store,writer,reader=Store(),Writer(),Reader(current or facts())
    record=practice.request_practice(store,writer,practice.Request(identity(),True),reader,NOW)
    return store,writer,reader,record


def event(record,**changes):
    return replace(practice.NormalPractice(record.card_scope(),1,99,'review',NOW+timedelta(seconds=1),
        NOW+timedelta(seconds=2)),**changes)


def test_verified_encounter_requests_ordinary_practice_not_priority_recall_or_review():
    store,writer,reader,record=accepted()
    assert record.state=='pending' and record.placement==practice.Placement('queued',queue_entry_id=42)
    assert writer.calls==[(identity(),'2026-10-20')] and record.completion_review_id is None
    assert not hasattr(record,'expires_at') and not hasattr(practice,'REASON')
    assert practice.request_practice(store,writer,record.request,reader,NOW)==record
    assert len(writer.calls)==1


@pytest.mark.parametrize('change,reason',[
    ({'mapping':'unknown'},'current_exact_encounter_required'),({'mapping':'ambiguous'},'current_exact_encounter_required'),
    ({'membership_ids':('other',)},'exclusive_current_membership_required'),
    ({'membership_ids':('repertoire','other')},'exclusive_current_membership_required'),
    ({'card_available':False},'current_introduced_review_card_required'),({'introduced':False},'current_introduced_review_card_required'),
    ({'integrity_current':False},'current_integrity_required'),
    ({'chronology':'introduced_before_game'},'outside_historical_exposure_scope'),
    ({'chronology':'unknown'},'chronology_uncertainty_confirmation_required'),
])
def test_unknown_invalid_or_unsafe_initial_request_never_admits(change,reason):
    store,writer,reader=Store(),Writer(),Reader(facts(**change))
    with pytest.raises(practice.ExposureRejected,match=reason):
        practice.request_practice(store,writer,practice.Request(identity(),True),reader,NOW)
    assert not store.rows and not writer.calls


def test_buried_verified_request_stays_pending_for_another_ordinary_opportunity():
    store,writer,reader,record=accepted(facts(buried=True))
    assert record.state=='pending' and record.placement.reason=='buried_today' and not writer.calls
    reader.facts=facts(queue_day='2026-10-21')
    practice.process_card(store,writer,reader,record.card_scope(),(),NOW+timedelta(days=1))
    assert len(writer.calls)==1 and store.find(record.request.encounter.key()).state=='pending'


def test_unknown_chronology_requires_explicit_confirmation():
    store,writer,reader=Store(),Writer(),Reader(facts(chronology='unknown'))
    record=practice.request_practice(store,writer,practice.Request(identity(),True,True),reader,NOW)
    assert record.state=='pending' and len(writer.calls)==1


def test_unserved_request_survives_rollover_removal_and_more_than_seven_days():
    store,writer,reader,record=accepted()
    reader.facts=facts(queue_day='2026-11-20')
    practice.process_card(store,writer,reader,record.card_scope(),(),NOW+timedelta(days=31))
    assert store.find(record.request.encounter.key()).state=='pending'
    assert writer.calls[-1][1]=='2026-11-20'


def test_any_verified_later_normal_practice_serves_even_after_placement_rebind():
    store,writer,reader,record=accepted();before=len(writer.calls)
    record=replace(record,placement=practice.Placement('queued',queue_entry_id=123));store.save(record)
    practice.process_card(store,writer,reader,record.card_scope(),(event(record),),NOW+timedelta(days=1))
    done=store.find(record.request.encounter.key())
    assert done.state=='served' and done.completion_review_id=='review'
    assert len(writer.calls)==before
    practice.process_card(store,writer,reader,record.card_scope(),(event(record),),NOW+timedelta(days=1))
    assert len(writer.calls)==before


@pytest.mark.parametrize('change',[
    {'scope':practice.CardScope('other','legacy','physical')},
    {'scope':practice.CardScope('target','other','physical')},{'card_revision':2},
    {'issued_at':NOW-timedelta(seconds=1)}, {'reviewed_at':NOW}, {'queue_entry_id':0}, {'review_id':''},
])
def test_other_scope_revision_or_pre_request_issuance_cannot_serve(change):
    store,writer,reader,record=accepted()
    practice.process_card(store,writer,reader,record.card_scope(),(event(record,**change),),NOW+timedelta(days=1))
    assert store.find(record.request.encounter.key()).state=='pending'


def test_cancel_never_calls_queue_owner_or_removes_already_admitted_work():
    store,writer,reader,record=accepted();before=list(writer.calls)
    done=practice.cancel_request(store,record,NOW)
    assert done.state=='cancelled' and done.placement.queue_entry_id==42 and writer.calls==before
    assert practice.cancel_request(store,record,NOW)==done
    practice.process_card(store,writer,reader,record.card_scope(),(),NOW+timedelta(days=1))
    assert writer.calls==before


def test_one_normal_practice_serves_multiple_pending_requests_without_re_admission():
    store,writer,reader,first=accepted()
    second_request=replace(first.request,encounter=replace(identity(),game_id='other-game'))
    second=practice.request_practice(store,writer,second_request,reader,NOW);before=len(writer.calls)
    result=practice.process_card(store,writer,reader,first.card_scope(),(event(first),),NOW+timedelta(days=1))
    assert {record.state for record in result}=={'served'} and len(writer.calls)==before


def test_cancel_one_request_does_not_discard_another_pending_request():
    store,writer,reader,first=accepted()
    second=practice.request_practice(store,writer,replace(first.request,encounter=replace(identity(),game_id='other')),reader,NOW)
    practice.cancel_request(store,first,NOW)
    assert store.pending_for_card(first.card_scope())==(second,)


def test_known_revision_change_supersedes_without_admitting_new_content():
    store,writer,reader,record=accepted();before=len(writer.calls)
    reader.facts=facts(current_revision=2,mapping='unknown',encounter=None)
    practice.process_card(store,writer,reader,record.card_scope(),(),NOW)
    assert store.find(record.request.encounter.key()).state=='superseded' and len(writer.calls)==before
