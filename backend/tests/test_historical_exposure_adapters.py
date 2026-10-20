"""Adapter projections/codecs with semantic source facts; no PostgreSQL claim."""
from copy import deepcopy
from datetime import datetime,timezone
from dataclasses import replace
import json
import pytest
from tempo_learning_review import historical_exposure as policy
from app.services.historical_exposure_adapter import CurrentFactReader,proof_document,ProtectedHeadWriter
from app.services.historical_exposure_source import project_encounter
from app.storage.historical_exposure import record_document,read_record,BorrowedExposureStore
from test_historical_exposure_source import fixture


def inputs():
    selection,captured=fixture();verified=project_encounter(selection,captured)
    proof=proof_document(verified)
    row={**verified.authority,'card_state':'learning','deleted':False,'blocked':False,'memberships':['ruy'],
         'canonical_prefix_moves_json':'[]'}
    row.update({'card_'+k:verified.card_source[k] for k in verified.card_source if k!='introduced_before_game'})
    row.update({'game_'+k:v for k,v in verified.game_header.items()})
    queue=dict(buried=False,protected=False,queue_day='2026-10-20',queue_entry_id=None,priority_class='none')
    return verified,proof,row,queue


def reader(verified,proof,row,queue,certificate=None):
    return CurrentFactReader({verified.request.encounter.key():proof},{verified.request.encounter.key():row},queue,
        {'ruy':verified.integrity_certificate if certificate is None else certificate})


def test_current_fact_projection_keeps_late_introduction_distinct_from_recall():
    verified,proof,row,queue=inputs()
    actual=reader(verified,proof,row,queue).current(verified.request)
    assert actual.mapping=='verified' and actual.encounter==verified.request.encounter
    assert actual.introduced and actual.chronology=='introduced_after_game' and actual.card_available
    assert actual.membership_ids==('ruy',) and actual.integrity_current


@pytest.mark.parametrize('changes',[{'catalogue_generation':99},{'graph_generation':99},{'graph_state':'building'},
    {'card_revision':2},{'game_content_hash':'c'*64},{'game_adaptive_excluded':1},
    {'card_moves_json':'["e2e4"]'}])
def test_source_fence_change_cannot_turn_retained_proof_into_current_authority(changes):
    verified,proof,row,queue=inputs();row.update(changes)
    actual=reader(verified,proof,row,queue).current(verified.request)
    assert actual.mapping=='unknown' and actual.encounter is None


@pytest.mark.parametrize('changes',[{'blocked':True},{'deleted':True},{'card_archived':1},
    {'card_pending_validation':1},{'card_state':'new'},{'card_superseded_by':'other'}])
def test_current_availability_requires_an_existing_introduced_review_card(changes):
    verified,proof,row,queue=inputs();row.update(changes)
    assert not reader(verified,proof,row,queue).current(verified.request).card_available


def test_current_integrity_certificate_and_membership_are_not_inferred_from_old_proof():
    verified,proof,row,queue=inputs();row['memberships']=['other','ruy']
    certificate={**verified.integrity_certificate,'scan_generation':'changed'}
    actual=reader(verified,proof,row,queue,certificate).current(verified.request)
    assert not actual.integrity_current and actual.membership_ids==('other','ruy')


def test_lifecycle_codec_roundtrip_has_no_expiry_or_recall_settlement_fields():
    verified,*_=inputs();record=policy.Record(verified.request,datetime(2026,10,20,tzinfo=timezone.utc),
        'active',policy.Placement('applied',queue_entry_id=42),'2026-10-20')
    value=record_document(record)
    assert read_record(json.loads(json.dumps(value)))==record
    assert 'expires_at' not in value and 'settlement' not in value


class Database:
    read_only=False
    def __init__(self,rows=()):self.rows,self.calls=rows,[]
    def execute_native(self,statement,parameters=()):
        self.calls.append((statement,parameters));return self
    def fetchone(self):return self.rows[0] if self.rows else None
    def fetchall(self):return self.rows


def test_borrowed_store_rejects_readonly_or_cross_scope_and_never_commits():
    verified,*_=inputs();scope=policy.CardScope('synthetic-target','legacy','physical-black')
    database=Database();database.read_only=True
    with pytest.raises(RuntimeError):BorrowedExposureStore(database,scope)
    database.read_only=False;store=BorrowedExposureStore(database,scope)
    with pytest.raises(policy.ExposureRejected,match='scope_mismatch'):
        store.active_for_card(replace(scope,workspace_id='other'))
    assert not database.calls


def test_store_uses_complete_inventory_or_refuses_without_truncating_candidates():
    scope=policy.CardScope('target','legacy','physical')
    database=Database([dict(record_json={})]*129);store=BorrowedExposureStore(database,scope)
    with pytest.raises(policy.ExposureRejected,match='complete_exposure_inventory'):
        store.active_for_card(scope)
    assert database.calls[0][1]==('target','legacy','physical')


def test_mutation_requires_existing_row_or_immutable_new_verified_proof():
    verified,*_=inputs();record=policy.Record(verified.request,datetime(2026,10,20,tzinfo=timezone.utc),
        'active',policy.Placement('deferred','protected_attempt_preserved',7),'2026-10-20')
    database=Database();store=BorrowedExposureStore(database,record.card_scope())
    with pytest.raises(policy.ExposureRejected,match='retained_exposure_required'):store.save(record)
    assert 'INSERT' not in database.calls[0][0]


def test_writer_scope_fence_runs_before_any_queue_effect():
    scope=policy.CardScope('target','legacy','physical');database=Database()
    writer=ProtectedHeadWriter(database,scope)
    with pytest.raises(policy.ExposureRejected,match='writer_scope'):
        writer.clear_priority(replace(scope,target_id='other'),'historical_exposure')
    assert not database.calls


def test_writer_calls_only_the_existing_canonical_protected_head_writer(monkeypatch):
    from app.services import historical_exposure_adapter as adapter,review_service
    verified,*_=inputs();identity=verified.request.encounter
    scope=policy.CardScope(identity.target_id,identity.workspace_id,identity.card_id)
    state={'id':7,'gameplay_priority_reason':None};calls=[]
    monkeypatch.setattr(adapter,'queued_entry',lambda *args:dict(state))
    def canonical(database,card_id,after,**options):
        calls.append((card_id,after,options));state['gameplay_priority_reason']=policy.REASON
    monkeypatch.setattr(review_service,'ensure_card_queued_after',canonical)
    result=ProtectedHeadWriter(Database(),scope).prioritize(identity,'2026-10-20',policy.REASON)
    assert result==policy.Placement('applied',queue_entry_id=7)
    assert calls==[(identity.card_id,0,dict(priority_reason=policy.REASON,queue_date='2026-10-20',
        protect_presented=True,preserve_order=False))]


@pytest.mark.parametrize('reason',['real_game_miss','legacy_real_game_miss','manual_priority'])
def test_writer_independently_preserves_any_unrelated_existing_priority(monkeypatch,reason):
    from app.services import historical_exposure_adapter as adapter,review_service
    verified,*_=inputs();identity=verified.request.encounter
    scope=policy.CardScope(identity.target_id,identity.workspace_id,identity.card_id)
    monkeypatch.setattr(adapter,'queued_entry',lambda *args:dict(id=7,gameplay_priority_reason=reason))
    monkeypatch.setattr(review_service,'ensure_card_queued_after',lambda *args,**kwargs:pytest.fail('Do not overwrite another priority'))
    assert ProtectedHeadWriter(Database(),scope).prioritize(identity,'2026-10-20',policy.REASON)==policy.Placement(
        'deferred','unrelated_priority_preserved',7)


def test_current_fact_factory_uses_raw_priority_even_if_order_projection_clears_stale_label(monkeypatch):
    from app.services import historical_exposure_adapter as adapter,postgres_integrity,queue_application
    from app import queue_position_lock
    verified,proof,row,_=inputs();identity=verified.request.encounter
    scope=policy.CardScope(identity.target_id,identity.workspace_id,identity.card_id);events=[]
    class Store:
        def __init__(self,*args):pass
        def active_for_card(self,scope):events.append('complete_inventory');return ()
        def source(self,request):return proof
    monkeypatch.setattr(adapter,'BorrowedExposureStore',Store)
    monkeypatch.setattr(adapter,'lock_source_inputs',lambda *args:events.append('source_locks'))
    monkeypatch.setattr(queue_position_lock,'lock_queue_date_for_position',lambda *args:events.append('day_lock'))
    monkeypatch.setattr(adapter,'current_source_rows',lambda *args:events.append('current_source') or {identity.key():row})
    monkeypatch.setattr(postgres_integrity,'current_integrity_certificate',lambda *args:verified.integrity_certificate)
    monkeypatch.setattr(queue_application,'presentation_rows',lambda *args:[dict(id=7,card_id=identity.card_id,
        position=0,admission_kind='review',content_type='opening',gameplay_priority_reason=None,issued=True)])
    monkeypatch.setattr(adapter,'queued_entry',lambda *args:dict(id=7,gameplay_priority_reason=adapter.BRIDGE_MISS_REASON))
    monkeypatch.setattr(adapter,'buried_today',lambda *args:False)
    _,_,facts_reader=adapter.locked_ports(Database(),scope,verified,'2026-10-20')
    actual=facts_reader.current(verified.request)
    assert actual.priority_class=='recall' and actual.protected and actual.queue_entry_id==7
    assert events==['complete_inventory','source_locks','day_lock','current_source']


def test_source_adapter_card_lock_order_and_target_scope_are_explicit():
    from app.storage.historical_exposure_facts import lock_card_scope
    scope=policy.CardScope('target','legacy','physical')
    class Locks(Database):
        def __init__(self):super().__init__([{'target_id':'target','workspace_id':'legacy'}])
    database=Locks();lock_card_scope(database,scope)
    assert 'prefix-transition:reservations' in database.calls[0][0]
    assert database.calls[1][1]==('tempo:card-edit:physical',)
    assert database.calls[2][1]==('physical',) and 'FOR UPDATE' in database.calls[2][0]
    with pytest.raises(ValueError,match='target/workspace'):
        lock_card_scope(database,replace(scope,target_id='other'))


def test_source_locks_after_card_are_nonblocking_to_avoid_reversed_waits():
    from app.storage.historical_exposure_facts import lock_source_inputs
    verified,*_=inputs()
    database=Database([(True,)])
    lock_source_inputs(database,(verified.request,))
    assert 'pg_try_advisory_xact_lock_shared' in database.calls[0][0]
    assert all('NOWAIT' in statement for statement,_ in database.calls[1:])
    database=Database([(False,)])
    from psycopg.errors import LockNotAvailable
    with pytest.raises(LockNotAvailable):lock_source_inputs(database,(verified.request,))
    assert len(database.calls)==1
