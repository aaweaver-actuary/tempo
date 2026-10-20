"""MVP receipt fulfillment, first issuance and durable per-card retry source proof."""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
from contextlib import contextmanager
import json
import sqlite3
import pytest
from tempo_learning_review import historical_exposure as policy
from app.storage.historical_exposure_practice import practice_from_row
from app.services import historical_exposure_refresh as refresh
from app.queue_attempt_origins import stamp_first_head_issuance
from test_historical_exposure_adapters import inputs
NOW=datetime(2026,10,20,12,tzinfo=timezone.utc)


def retained():
    verified,*_=inputs()
    return policy.Record(verified.request,NOW-timedelta(seconds=10),'pending',policy.Placement('queued',queue_entry_id=42),'2026-10-20')


def proof():
    record=retained()
    return dict(card_id=record.card_scope().card_id,revision=1,queue_entry_id=99,review_id=77,
        first_issued_at=(NOW-timedelta(seconds=5)).isoformat(),reviewed_at=NOW.isoformat(),
        request_json='{"expected_revision":1}',origin_result='{"review_id":77}',source_kind='study',
        source_ref='attempt:normal',attempt_id='normal',scheduling_status='canonical',invalidated_at=None,
        issued_as_head=1,origin_status='complete')


def test_any_later_normal_receipt_serves_without_exposure_specific_entry_history():
    record=retained();event=practice_from_row(record.card_scope(),record,proof(),NOW)
    assert event==policy.NormalPractice(record.card_scope(),1,99,'77',NOW-timedelta(seconds=5),NOW)


@pytest.mark.parametrize('change',[
    {'card_id':'other'},{'revision':2},{'request_json':'{}'},{'invalidated_at':'invalid'},
    {'source_kind':'gameplay'},{'source_ref':'attempt:other'},{'scheduling_status':'conflict'},
    {'issued_as_head':0},{'first_issued_at':None},{'first_issued_at':(NOW-timedelta(days=1)).isoformat()},
    {'reviewed_at':(NOW-timedelta(days=1)).isoformat()},{'origin_result':'{"review_id":99}'},
])
def test_old_unknown_or_unbound_receipts_cannot_fulfill_pending_request(change):
    record=retained()
    assert practice_from_row(record.card_scope(),record,{**proof(),**change},NOW) is None


def test_first_issued_time_is_once_canonical_and_not_retrofitted_or_refreshed():
    db=sqlite3.connect(':memory:')
    db.executescript('''CREATE TABLE queue_attempt_origins(queue_entry_id INTEGER,card_id TEXT,revision INTEGER,
        issued_as_head INTEGER,first_issued_at TEXT); INSERT INTO queue_attempt_origins VALUES(1,'physical',1,0,NULL);
        INSERT INTO queue_attempt_origins VALUES(2,'physical',1,1,NULL);''')
    assert stamp_first_head_issuance(db,1,'physical',1)==1
    first=db.execute('SELECT first_issued_at FROM queue_attempt_origins WHERE queue_entry_id=1').fetchone()[0]
    assert datetime.fromisoformat(first).tzinfo is not None
    assert stamp_first_head_issuance(db,1,'physical',1)==1
    assert db.execute('SELECT first_issued_at FROM queue_attempt_origins WHERE queue_entry_id=1').fetchone()[0]==first
    stamp_first_head_issuance(db,2,'physical',1)
    assert db.execute('SELECT first_issued_at FROM queue_attempt_origins WHERE queue_entry_id=2').fetchone()[0] is None
    assert stamp_first_head_issuance(db,1,'other',1)==0


def test_first_issuance_stamp_rolls_back_with_the_caller():
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE queue_attempt_origins(queue_entry_id INTEGER,card_id TEXT,revision INTEGER,issued_as_head INTEGER,first_issued_at TEXT)')
    db.execute("INSERT INTO queue_attempt_origins VALUES(1,'physical',1,0,NULL)")
    db.execute('SAVEPOINT caller');stamp_first_head_issuance(db,1,'physical',1)
    db.execute('ROLLBACK TO caller');db.execute('RELEASE caller')
    assert db.execute('SELECT issued_as_head,first_issued_at FROM queue_attempt_origins').fetchone()==(0,None)


def test_foreground_review_only_wakes_pending_work_not_source_verification(monkeypatch):
    from app.postgres_store import PostgresConnection
    db=PostgresConnection(object());scope=retained().card_scope();calls=[]
    monkeypatch.setattr(refresh,'pending_scope_for_card',lambda *args:scope)
    monkeypatch.setattr(refresh,'schedule_refresh',lambda *args,**kwargs:calls.append(args))
    monkeypatch.setattr(refresh,'locked_ports',lambda *args:pytest.fail('No source verification in review'))
    refresh.after_study_review(db,scope.card_id)
    assert calls==[(db,scope)]


@pytest.mark.parametrize('pending',[True,False])
def test_worker_consumes_normal_practice_then_retries_only_while_pending(monkeypatch,pending):
    record=retained();scope=record.card_scope();calls=[]
    class Store:
        def pending_for_card(self,supplied):return (record,) if pending else ()
    store=Store()
    @contextmanager
    def connection(**kwargs):yield object()
    monkeypatch.setattr(refresh,'connection',connection)
    monkeypatch.setattr(refresh.activity_gate,'wait_for_foreground',lambda:None)
    monkeypatch.setattr(refresh,'lock_card_scope',lambda *args:calls.append('card'))
    monkeypatch.setattr(refresh,'locked_ports',lambda *args:(store,'writer','reader'))
    monkeypatch.setattr(refresh,'lock_current_slice',lambda *args:True)
    monkeypatch.setattr(refresh,'completed_practices',lambda *args:calls.append('normal_receipts') or ())
    monkeypatch.setattr(refresh.policy,'process_card',lambda *args:calls.append('process'))
    monkeypatch.setattr(refresh,'schedule_refresh',lambda *args,**kwargs:calls.append('retry'))
    monkeypatch.setattr(refresh,'complete_task_slice_in_transaction',lambda *args:calls.append('complete'))
    assert refresh.execute_exposure_refresh_slice({'payload':dict(target_id=scope.target_id,workspace_id='legacy',card_id=scope.card_id)}) is pending
    assert calls==['card','normal_receipts','process','retry' if pending else 'complete']


def test_cancel_yesterday_request_performs_no_admission_and_retains_other_pending(monkeypatch):
    from app import historical_exposure_commands as commands
    from app.historical_exposure_contracts import ExposureControl
    record=retained();other=replace(record,request=replace(record.request,encounter=replace(record.request.encounter,game_id='other')))
    rows={record.request.encounter.key():record,other.request.encounter.key():other}
    class Store:
        def find(self,key):return rows.get(key)
        def save(self,value):rows[value.request.encounter.key()]=value
    store=Store()
    monkeypatch.setattr(commands,'lock_card_scope',lambda *args:None)
    monkeypatch.setattr(commands,'BorrowedExposureStore',lambda *args:store)
    monkeypatch.setattr(commands,'locked_ports',lambda *args:pytest.fail('Cancel must not admit or read source facts'))
    monkeypatch.setattr(commands,'schedule_refresh',lambda *args:pytest.fail('Cancel must not create work'))
    control=ExposureControl(target_id=record.card_scope().target_id,workspace_id='legacy',card_id=record.card_scope().card_id,
        encounter_key=record.request.encounter.key())
    result=commands.cancel_exposure(object(),control)
    assert result['state']=='cancelled' and result['last_admission']['queue_entry_id']==42
    assert rows[other.request.encounter.key()]==other
