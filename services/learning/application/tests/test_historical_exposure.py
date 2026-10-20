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


class Store:
    def __init__(self): self.rows, self.saves = {}, []
    def find(self, key): return self.rows.get(key)
    def save(self, record):
        self.rows[record.request.encounter.key()] = record
        self.saves.append(record)
    def another_active(self, card_id, excluding, now):
        return any(key!=excluding and row.request.encounter.card_id==card_id and row.priority_active(now)
                   for key,row in self.rows.items())


class Writer:
    def __init__(self): self.calls, self.cleared = [], []
    def prioritize(self, identity, queue_day, reason):
        self.calls.append((identity,queue_day,reason))
        return exposure.Placement('applied',queue_entry_id=42)
    def clear_priority(self, card_id, reason): self.cleared.append((card_id,reason))


def accepted(**changes):
    store, writer = Store(), Writer()
    request = exposure.Request(encounter(),True)
    record = exposure.request_priority(store,writer,request,facts(**changes),NOW)
    return store, writer, record


def test_explicit_historical_departure_prioritizes_practice_without_miss_or_review():
    store, writer, record = accepted()
    assert record.state=='active' and record.expires_at == NOW+timedelta(days=7)
    assert writer.calls == [(encounter(),'2026-10-20','historical_exposure')]
    assert record.request.encounter.observed_move=='g1f3' and record.completion_review_id is None
    assert len(store.saves)==1 and not writer.cleared


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
    store, writer = Store(), Writer()
    with pytest.raises(exposure.ExposureRejected,match=reason):
        exposure.request_priority(store,writer,exposure.Request(encounter(),True),facts(**change),NOW)
    assert not store.saves and not writer.calls


def test_explicit_user_intent_and_chronology_uncertainty_are_separate():
    store, writer = Store(), Writer()
    with pytest.raises(exposure.ExposureRejected,match='explicit_user_request_required'):
        exposure.request_priority(store,writer,exposure.Request(encounter(),False),facts(),NOW)
    request = exposure.Request(encounter(),True,accepts_unknown_chronology=True)
    assert exposure.request_priority(store,writer,request,facts(chronology='unknown'),NOW).state=='active'


def test_protected_target_is_retained_without_moving_or_issuing_any_attempt():
    _, writer, record = accepted(protected=True,queue_entry_id=7)
    assert record.placement == exposure.Placement('deferred','protected_attempt_preserved',7)
    assert not writer.calls and not writer.cleared


def test_response_loss_replay_has_original_deadline_and_no_additional_effect():
    store, writer, record = accepted()
    assert exposure.request_priority(store,writer,record.request,facts(),NOW+timedelta(days=8))==record
    assert len(store.saves)==len(writer.calls)==1
    assert not record.priority_active(NOW+timedelta(days=8))


def test_reused_encounter_cannot_change_confirmation_or_revive_intent():
    store, writer, record = accepted()
    with pytest.raises(exposure.ExposureRejected,match='exposure_intent_reused'):
        exposure.request_priority(store,writer,replace(record.request,accepts_unknown_chronology=True),facts(),NOW)
    assert len(writer.calls)==1


def test_refresh_reuses_current_priority_and_reapplies_same_finite_intent_next_day():
    store, writer, record = accepted()
    assert exposure.refresh_priority(store,writer,record,facts(queue_entry_id=42,priority_present=True),NOW)==record
    assert len(writer.calls)==1
    updated=exposure.refresh_priority(store,writer,record,facts(queue_day='2026-10-21'),NOW+timedelta(days=1))
    assert len(writer.calls)==2 and updated.expires_at==record.expires_at


@pytest.mark.parametrize('change', [{'buried':True},{'integrity_current':False}])
def test_temporary_ineligibility_clears_only_exposure_label_without_unburying(change):
    store, writer, record = accepted()
    updated=exposure.refresh_priority(store,writer,record,facts(**change),NOW)
    assert updated.state=='active' and updated.placement.status=='deferred'
    assert len(writer.calls)==1 and writer.cleared==[('physical','historical_exposure')]


def test_expiry_and_source_revocation_are_terminal_without_review_or_queue_deletion():
    for when,current,state in [(NOW+timedelta(days=7),facts(),'expired'),
            (NOW,facts(encounter=replace(encounter(),card_revision=2)),'revoked')]:
        store, writer, record = accepted()
        ended=exposure.refresh_priority(store,writer,record,current,when)
        assert ended.state==state and ended.completion_review_id is None
        assert writer.cleared==[('physical','historical_exposure')]
        assert exposure.refresh_priority(store,writer,ended,facts(),when)==ended
        assert len(writer.calls)==1


def completion(**changes):
    return replace(exposure.PracticeCompletion('physical',1,42,'review-1',NOW,'study',True),**changes)


@pytest.mark.parametrize('change',[{'card_revision':2},{'queue_entry_id':99},{'source_kind':'gameplay'},
    {'verified_normal_attempt':False},{'reviewed_at':NOW-timedelta(seconds=1)},
    {'reviewed_at':NOW+timedelta(seconds=1)},{'review_id':''}])
def test_only_actual_subsequent_bound_study_practice_can_complete_priority(change):
    store, writer, record=accepted()
    assert exposure.complete_practice(store,writer,record,completion(**change),NOW)==record
    assert not writer.cleared and len(store.saves)==1


def test_normal_practice_completion_is_exactly_once_not_a_remediation_settlement():
    store, writer, record=accepted()
    done=exposure.complete_practice(store,writer,record,completion(),NOW)
    assert done.state=='completed' and done.completion_review_id=='review-1'
    assert not done.priority_active(NOW)
    assert exposure.complete_practice(store,writer,done,completion(),NOW)==done
    assert exposure.complete_practice(store,writer,record,completion(),NOW)==done
    assert len(store.saves)==2 and writer.cleared==[('physical','historical_exposure')]


def test_one_completed_exposure_does_not_clear_another_active_request_for_same_card():
    store, writer, record=accepted()
    other=replace(record.request,encounter=replace(encounter(),game_id='second-game'))
    exposure.request_priority(store,writer,other,facts(encounter=other.encounter),NOW)
    exposure.complete_practice(store,writer,record,completion(),NOW)
    assert not writer.cleared


def test_same_game_instant_in_another_offset_has_same_idempotent_identity():
    shifted=replace(encounter(),occurred_at=encounter().occurred_at.astimezone(timezone(timedelta(hours=-4))))
    assert shifted.key()==encounter().key()
    with pytest.raises(exposure.ExposureRejected,match='timezone_required'):
        replace(encounter(),occurred_at=datetime(2026,10,9)).key()
