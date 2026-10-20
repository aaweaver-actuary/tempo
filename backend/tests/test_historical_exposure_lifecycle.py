"""Trusted receipt projection and yielding lifecycle composition; no native DB."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime,timezone,timedelta
import json
import pytest
from tempo_learning_review import historical_exposure as policy
from app.storage.historical_exposure_practice import practice_from_row,completed_practices
from app.services import historical_exposure_refresh as refresh
from test_historical_exposure_adapters import inputs,Database

NOW=datetime(2026,10,20,12,tzinfo=timezone.utc)


def retained():
    verified,*_=inputs()
    record=policy.Record(verified.request,NOW-timedelta(seconds=10),'active',
        policy.Placement('applied',queue_entry_id=42),'2026-10-20')
    return record


def proof():
    record=retained()
    return dict(card_id=record.card_scope().card_id,queue_entry_id=42,revision=1,
        reviewed_at=NOW.isoformat(),request_json=json.dumps(dict(expected_revision=1)),
        origin_result=json.dumps(dict(review_id=77)),source_kind='study',invalidated_at=None,
        source_ref='attempt:actual-attempt',attempt_id='actual-attempt',scheduling_status='canonical',
        issued_as_head=1,origin_status='complete',review_id=77)


def test_practice_comes_from_bound_canonical_review_receipt_not_submission_flags():
    record=retained();event=practice_from_row(record.card_scope(),record,proof(),NOW)
    assert event==policy.PracticeCompletion(record.card_scope().card_id,1,42,'77',NOW,'study',True,record.card_scope())


@pytest.mark.parametrize('change',[
    dict(card_id='other'),dict(queue_entry_id=99),dict(revision=2),
    dict(request_json='{}'),dict(request_json='{"expected_revision":2}'),
    dict(origin_result='{"review_id":99}'),dict(source_kind='gameplay'),
    dict(invalidated_at='invalidated'),dict(source_ref='attempt:another-attempt'),
    dict(scheduling_status='conflict'),dict(issued_as_head=0),dict(origin_status='buried'),
    dict(reviewed_at=(NOW-timedelta(days=1)).isoformat()),
    dict(reviewed_at=(NOW+timedelta(seconds=1)).isoformat()),dict(reviewed_at='2026-10-20T12:00:00'),
    dict(request_json='not-json'),dict(origin_result='not-json'),
])
def test_unverified_old_future_unissued_or_other_identity_review_never_serves(change):
    record=retained()
    assert practice_from_row(record.card_scope(),record,{**proof(),**change},NOW) is None


def test_complete_practice_projection_refuses_missing_or_other_keys():
    record=retained();scope=record.card_scope();key=record.request.encounter.key()
    for rows in [[],[dict(key='other',proof=proof())]]:
        with pytest.raises(policy.ExposureRejected,match='complete_practice_projection'):
            completed_practices(Database(rows),scope,(record,),NOW)
    assert completed_practices(Database([dict(key=key,proof=proof())]),scope,(record,),NOW)[0].review_id=='77'


@pytest.mark.parametrize('remaining',[True,False])
def test_worker_lock_fence_trusted_completion_and_single_reconciliation_before_checkpoint(monkeypatch,remaining):
    record=retained();scope=record.card_scope();events=[];db=object()
    class Store:
        active=True
        def active_for_card(self,supplied):
            assert supplied==scope;events.append('inventory');return (record,) if self.active else ()
    store=Store()
    @contextmanager
    def connection(*,background):
        assert background;events.append('open')
        try:yield db
        finally:events.append('closed')
    def ports(database,supplied):
        assert database is db and supplied==scope;events.append('ports_source_then_day');return store,'writer','reader'
    def reconcile(st,writer,reader,supplied,completions,now):
        assert (st,writer,reader,supplied,completions)==(store,'writer','reader',scope,('trusted',))
        events.append('reconcile');store.active=remaining
    monkeypatch.setattr(refresh,'connection',connection)
    monkeypatch.setattr(refresh.activity_gate,'wait_for_foreground',lambda:events.append('yield'))
    monkeypatch.setattr(refresh,'lock_card_scope',lambda *args:events.append('card_lock'))
    monkeypatch.setattr(refresh,'locked_ports',ports)
    monkeypatch.setattr(refresh,'lock_current_slice',lambda *args:events.append('task_fence') or True)
    monkeypatch.setattr(refresh,'completed_practices',lambda *args:events.append('trusted_receipts') or ('trusted',))
    monkeypatch.setattr(refresh.policy,'refresh_card_priority',reconcile)
    monkeypatch.setattr(refresh,'schedule_refresh',lambda database,supplied,**options:events.append(('next_lease',options)))
    monkeypatch.setattr(refresh,'complete_task_slice_in_transaction',lambda *args:events.append('complete_lease'))
    assert refresh.execute_exposure_refresh_slice(dict(payload=dict(target_id=scope.target_id,
        workspace_id=scope.workspace_id,card_id=scope.card_id))) is remaining
    assert events[:7]==['yield','open','card_lock','ports_source_then_day','task_fence','inventory','trusted_receipts']
    assert events.index('reconcile')>events.index('trusted_receipts')
    assert events[-2:]==[('next_lease',dict(delay_seconds=60)) if remaining else 'complete_lease','closed']


def test_stale_worker_does_not_read_completion_or_publish_effects(monkeypatch):
    record=retained();events=[]
    @contextmanager
    def connection(**kwargs):yield object()
    monkeypatch.setattr(refresh,'connection',connection)
    monkeypatch.setattr(refresh.activity_gate,'wait_for_foreground',lambda:None)
    monkeypatch.setattr(refresh,'lock_card_scope',lambda *args:None)
    monkeypatch.setattr(refresh,'locked_ports',lambda *args:('store','writer','reader'))
    monkeypatch.setattr(refresh,'lock_current_slice',lambda *args:False)
    monkeypatch.setattr(refresh,'completed_practices',lambda *args:pytest.fail('Stale worker cannot consume receipts'))
    assert refresh.execute_exposure_refresh_slice(dict(payload=dict(target_id=record.card_scope().target_id,
        workspace_id='legacy',card_id=record.card_scope().card_id))) is False


def test_request_wake_uses_scope_only_and_lease_delay_never_an_intent_expiry(monkeypatch):
    calls=[];scope=retained().card_scope();database=object()
    monkeypatch.setattr(refresh,'enqueue_compact_postgres_task_in_transaction',
        lambda *args,**kwargs:calls.append((args,kwargs)))
    refresh.schedule_refresh(database,scope,delay_seconds=60)
    args,options=calls[0]
    assert args[0] is database and args[1]=='historical_exposure_refresh'
    assert args[3]==dict(target_id=scope.target_id,workspace_id='legacy',card_id=scope.card_id)
    assert options==dict(priority=30,delay_seconds=60)
    assert 'expires' not in json.dumps(args[3])


def test_trusted_practice_same_local_identifiers_in_another_target_cannot_serve():
    record=retained();other=replace(record.card_scope(),target_id='other-target')
    assert practice_from_row(other,record,proof(),NOW) is None


def test_foreground_review_only_queues_wake_and_does_not_verify_sources_or_reconcile(monkeypatch):
    from app.postgres_store import PostgresConnection
    database=PostgresConnection(object());scope=retained().card_scope();events=[]
    monkeypatch.setattr(refresh,'active_scope_for_card',lambda db,card_id:events.append('active_scope') or scope)
    monkeypatch.setattr(refresh,'schedule_refresh',lambda db,supplied:events.append(('wake',supplied)))
    monkeypatch.setattr(refresh,'locked_ports',lambda *args:pytest.fail('Foreground must not run source verification'))
    monkeypatch.setattr(refresh,'completed_practices',lambda *args:pytest.fail('Foreground must not reconcile completion'))
    refresh.after_study_review(database,scope.card_id)
    assert events==['active_scope',('wake',scope)]


def test_cancel_uses_retained_scope_and_consumes_committed_practice_before_cancelling(monkeypatch):
    from app import historical_exposure_commands as commands
    from app.historical_exposure_contracts import ExposureControl
    record=retained();scope=record.card_scope();events=[];database=object()
    class Store:
        def __init__(self,*args):pass
        def find(self,key):return record
    monkeypatch.setattr(commands,'lock_card_scope',lambda *args:events.append('card_lock'))
    monkeypatch.setattr(commands,'BorrowedExposureStore',Store)
    monkeypatch.setattr(refresh,'refresh_locked_card',lambda *args,**kwargs:events.append('trusted_refresh') or ('store','writer','reader'))
    def cancel(store,writer,current,reader,now):
        assert (store,writer,current,reader)==('store','writer',record,'reader')
        events.append('cancel');return replace(record,state='cancelled',terminal_reason='user_cancelled')
    monkeypatch.setattr(commands.policy,'cancel_priority',cancel)
    control=ExposureControl(target_id=scope.target_id,workspace_id='legacy',card_id=scope.card_id,
        encounter_key=record.request.encounter.key())
    assert commands.cancel_exposure(database,control)['state']=='cancelled'
    assert events==['card_lock','trusted_refresh','cancel']


def test_study_only_admission_allows_explicit_practice_refresh_but_not_analysis():
    import sqlite3
    from app.services.defensive_analysis import task_admission_sql
    from app.services.study_mode import STUDY_TASK_KINDS
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE settings(id INTEGER,study_only_mode INTEGER,defensive_analysis_enabled INTEGER)')
    db.execute('INSERT INTO settings VALUES(1,1,0)')
    assert 'historical_exposure_refresh' in STUDY_TASK_KINDS
    for kind,allowed in [('historical_exposure_refresh',1),('daily_queue',1),('game_derivation_compare',0),('defensive_threat_scan',0)]:
        assert db.execute('SELECT '+task_admission_sql('?'),(kind,kind)).fetchone()[0]==allowed
