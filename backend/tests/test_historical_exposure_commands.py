"""Real command-gateway savepoints with semantic fake exposure/queue ports.

Only the receipt harness uses in-memory SQLite. No proposed schema migration,
PostgreSQL lock or actual queue writer is exercised by these source cases.
"""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime,timezone
import json
import sqlite3

import pytest
from fastapi import HTTPException
from psycopg.errors import LockNotAvailable
from tempo_learning_review import historical_exposure as policy
from app import command_gateway as gateway
from app import historical_exposure_commands as commands
from app.services.redis_admission_gate import BackgroundAdmissionDeferred
from app.storage.historical_exposure import record_document,read_record
from test_historical_exposure_adapters import inputs

COMMAND='historical_exposure.request'


class ReceiptDatabase:
    read_only=False
    def __init__(self):
        self.raw=self;self.events=[];self.connection=sqlite3.connect(':memory:')
        self.connection.executescript('''
            CREATE TABLE operation_receipts(operation_id TEXT PRIMARY KEY,command_name TEXT,request_hash TEXT,
                state TEXT,response_json TEXT,error_json TEXT,attempt_token TEXT,lease_expires_at TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE fake_intents(encounter_key TEXT PRIMARY KEY,record_json TEXT NOT NULL);
            CREATE TABLE fake_queue(card_id TEXT PRIMARY KEY,id INTEGER,position INTEGER,reason TEXT);
            CREATE TABLE fake_wakes(card_id TEXT PRIMARY KEY);
            INSERT INTO fake_queue VALUES('protected-head',1,0,NULL);
        ''')
    def execute(self,statement,parameters=()):
        # Existing command-gateway SQL is exercised through its portable receipt
        # harness, not treated as evidence that PostgreSQL locks work.
        if 'pg_advisory_xact_lock' in statement:
            self.events.append('operation_lock');return self.connection.execute('SELECT 1')
        return self.connection.execute(statement.replace('%s','?').replace(' FOR UPDATE','').replace('NOW()','CURRENT_TIMESTAMP'),parameters)


class Store:
    def __init__(self,database,scope):self.database,self.scope=database,scope
    def find(self,key):
        row=self.database.connection.execute('SELECT record_json FROM fake_intents WHERE encounter_key=?',(key,)).fetchone()
        return read_record(row[0]) if row else None
    def save(self,record):
        assert record.card_scope()==self.scope
        self.database.connection.execute('INSERT INTO fake_intents VALUES(?,?) ON CONFLICT(encounter_key) DO UPDATE SET record_json=excluded.record_json',
            (record.request.encounter.key(),json.dumps(record_document(record))))
    def active_for_card(self,scope):
        assert scope==self.scope
        return tuple(record for row in self.database.connection.execute('SELECT record_json FROM fake_intents')
                     if (record:=read_record(row[0])).state=='active' and record.card_scope()==scope)


class Reader:
    def __init__(self,database,verified):self.database,self.verified=database,verified
    def current(self,request):
        self.database.events.append('current_facts')
        row=self.database.connection.execute('SELECT id,reason FROM fake_queue WHERE card_id=?',
            (request.encounter.card_id,)).fetchone()
        return policy.CurrentFacts(request.encounter,'verified',(request.encounter.repertoire_id,),True,True,True,
            'introduced_after_game',False,False,'2026-10-20',row[0] if row else None,
            'exposure' if row and row[1]==policy.REASON else 'none')


class Writer:
    def __init__(self,database,fail=False):self.database,self.fail=database,fail
    def prioritize(self,identity,day,reason):
        self.database.events.append('queue_write')
        self.database.connection.execute('INSERT INTO fake_queue VALUES(?,2,1,?) ON CONFLICT(card_id) DO UPDATE SET reason=excluded.reason',
            (identity.card_id,reason))
        if self.fail:raise ValueError('Controlled after-queue failure')
        return policy.Placement('applied',queue_entry_id=2)
    def clear_priority(self,scope,reason):
        self.database.connection.execute('UPDATE fake_queue SET reason=NULL WHERE card_id=? AND reason=?',(scope.card_id,reason))


@pytest.fixture
def case(monkeypatch):
    verified,*_=inputs();database=ReceiptDatabase();prepared=commands.PreparedExposure(verified.request,verified)
    def prepare(payload):database.events.append('prepare');return prepared
    @contextmanager
    def writer_connection(background):
        assert not background
        database.events.append('writer_open')
        try:yield database
        except BaseException:database.connection.rollback();raise
        else:database.connection.commit()
    def lock_scope(db,scope):
        assert scope==policy.CardScope(verified.request.encounter.target_id,'legacy','physical-black')
        db.events.append('card_lock')
    def ports(db,scope,supplied):
        assert database.events.index('card_lock')<len(database.events)
        return Store(db,scope),Writer(db),Reader(db,supplied)
    monkeypatch.setattr(gateway,'_writer_connection',writer_connection)
    monkeypatch.setitem(gateway._preparers,COMMAND,prepare)
    monkeypatch.setattr(commands,'lock_card_scope',lock_scope)
    monkeypatch.setattr(commands,'BorrowedExposureStore',Store)
    monkeypatch.setattr(commands,'locked_ports',ports)
    monkeypatch.setattr(commands,'schedule_refresh',lambda db,scope:db.connection.execute(
        'INSERT INTO fake_wakes VALUES(?) ON CONFLICT DO NOTHING',(scope.card_id,)))
    yield database,prepared
    database.connection.close()


def payload():return {'selection':{'synthetic':'stable immutable receipt payload'}}


def test_registered_prepare_closes_before_writer_and_receipt_replay_is_quiet(case):
    database,prepared=case
    result=gateway.execute_command('op',COMMAND,payload())
    assert result['reason']=='historical_exposure' and result['placement']['queue_entry_id']==2
    assert database.events.index('prepare')<database.events.index('writer_open')<database.events.index('card_lock')
    assert database.events.index('card_lock')<database.events.index('current_facts')<database.events.index('queue_write')
    before=database.connection.execute('SELECT * FROM fake_queue ORDER BY position').fetchall()
    assert before[0]==('protected-head',1,0,None)
    assert len(database.connection.execute('SELECT * FROM fake_intents').fetchall())==1
    assert gateway.execute_command('op',COMMAND,payload())==result
    assert database.events.count('queue_write')==1
    assert database.connection.execute('SELECT COUNT(*) FROM fake_wakes').fetchone()[0]==1
    assert database.connection.execute('SELECT * FROM fake_queue ORDER BY position').fetchall()==before
    assert database.connection.execute('SELECT state FROM operation_receipts').fetchone()[0]=='complete'


def test_different_operations_for_same_intent_reuse_existing_record_and_queue_entry(case):
    database,_=case
    result=gateway.execute_command('op-a',COMMAND,payload())
    assert gateway.execute_command('op-b',COMMAND,payload())==result
    assert database.events.count('queue_write')==1
    assert database.connection.execute('SELECT COUNT(*) FROM fake_intents').fetchone()[0]==1
    assert database.connection.execute('SELECT COUNT(*) FROM fake_queue').fetchone()[0]==2
    assert database.connection.execute('SELECT COUNT(*) FROM operation_receipts WHERE state=\'complete\'').fetchone()[0]==2


@pytest.mark.parametrize('failure_stage',['after_queue','after_record'])
def test_actual_gateway_savepoint_rolls_back_intent_and_queue_before_failure_receipt(case,monkeypatch,failure_stage):
    database,_=case;original=commands.locked_ports
    if failure_stage=='after_queue':
        def failed_ports(*args):
            store,_,reader=original(*args);return store,Writer(database,fail=True),reader
        monkeypatch.setattr(commands,'locked_ports',failed_ports)
    else:
        monkeypatch.setattr(commands,'response',lambda record:(_ for _ in ()).throw(ValueError('Controlled after-record failure')))
    assert gateway.execute_command('op',COMMAND,payload()) is None
    assert database.connection.execute('SELECT COUNT(*) FROM fake_intents').fetchone()[0]==0
    assert database.connection.execute('SELECT * FROM fake_queue').fetchall()==[('protected-head',1,0,None)]
    assert database.connection.execute('SELECT state FROM operation_receipts').fetchone()[0]=='failed'


def test_same_operation_payload_conflict_cannot_create_a_second_effect(case):
    database,_=case;gateway.execute_command('op',COMMAND,payload())
    with pytest.raises(gateway.CommandConflict):gateway.execute_command('op',COMMAND,{'selection':{'changed':True}})
    assert database.events.count('queue_write')==1


def test_source_lock_contention_yields_retained_operation_without_false_receipt(case,monkeypatch):
    database,_=case
    def busy(*args):raise LockNotAvailable('Controlled busy source')
    monkeypatch.setattr(commands,'locked_ports',busy)
    with pytest.raises(BackgroundAdmissionDeferred):gateway.execute_command('op',COMMAND,payload())
    assert database.connection.execute('SELECT COUNT(*) FROM operation_receipts').fetchone()[0]==0
    assert database.connection.execute('SELECT COUNT(*) FROM fake_intents').fetchone()[0]==0
    assert database.events.count('queue_write')==0


def test_retained_selection_replay_preparation_does_not_recapture_changed_source(monkeypatch):
    verified,*_=inputs();record=policy.Record(verified.request,datetime.now(timezone.utc),
        'completed',policy.Placement('applied',queue_entry_id=2),'2026-10-20','review-1','practice_served')
    monkeypatch.setattr(commands,'retained_request_for_selection',lambda selection:record.request)
    monkeypatch.setattr(commands,'prepare_selection',lambda *args:pytest.fail('Retained immutable request must not recapture'))
    prepared=commands.prepare_request({'selection':verified.selection})
    assert prepared.request==verified.request and prepared.verified is None


def test_preparation_invalid_selection_has_explicit_error_before_source_read(monkeypatch):
    monkeypatch.setattr(commands,'retained_request_for_selection',lambda *args:pytest.fail('Invalid input must not read'))
    with pytest.raises(HTTPException) as error:commands.prepare_request({'selection':{}})
    assert error.value.status_code==422
