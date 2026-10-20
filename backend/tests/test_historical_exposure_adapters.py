"""Verified facts, borrowed storage and ordinary canonical queue effects."""
from dataclasses import replace
from datetime import datetime,timezone
import sqlite3
import pytest
from tempo_learning_review import historical_exposure as policy
from app.services.historical_exposure_adapter import CurrentFactReader,proof_document,OrdinaryQueueWriter
from app.services.historical_exposure_source import project_encounter
from app.storage.historical_exposure import record_document,read_record,BorrowedExposureStore
from test_historical_exposure_source import fixture


def inputs():
    selection,captured=fixture();verified=project_encounter(selection,captured)
    proof=proof_document(verified)
    row={**verified.authority,'card_state':'learning','deleted':False,'blocked':False,'memberships':['ruy'],
        'canonical_prefix_moves_json':'[]'}
    row.update({'card_'+key:value for key,value in verified.card_source.items() if key!='introduced_before_game'})
    row.update({'game_'+key:value for key,value in verified.game_header.items()})
    return verified,proof,row,dict(buried=False,queue_day='2026-10-20')


class Database:
    read_only=False
    def __init__(self,rows=()):self.rows=list(rows);self.calls=[]
    def execute_native(self,statement,parameters=()):self.calls.append((statement,parameters));return self
    execute=execute_native
    def fetchone(self):return self.rows[0] if self.rows else None
    def fetchall(self):return self.rows


def test_current_projection_keeps_late_introduction_distinct_from_recall():
    verified,proof,row,queue=inputs()
    facts=CurrentFactReader({verified.request.encounter.key():proof},{verified.request.encounter.key():row},queue,
        {'ruy':verified.integrity_certificate}).current(verified.request)
    assert facts.mapping=='verified' and facts.chronology=='introduced_after_game' and facts.current_revision==1


@pytest.mark.parametrize('change',[{'card_revision':2},{'scope_source_revision':99},{'graph_generation':99},
    {'card_moves_json':'["h7h6"]'},{'game_content_hash':'c'*64}])
def test_changed_current_source_is_not_verified_from_old_proof(change):
    verified,proof,row,queue=inputs()
    facts=CurrentFactReader({verified.request.encounter.key():proof},{verified.request.encounter.key():{**row,**change}},queue,
        {'ruy':verified.integrity_certificate}).current(verified.request)
    assert facts.mapping=='unknown' and facts.encounter is None


def test_borrowed_codec_keeps_scope_and_has_no_expiry_priority_or_binding_ledger():
    verified,*_=inputs()
    record=policy.Record(verified.request,datetime(2026,10,20,tzinfo=timezone.utc),'pending',
        policy.Placement('queued',queue_entry_id=42),'2026-10-20')
    assert read_record(record_document(record))==record
    assert not {'expires_at','priority_reason','attempt_bindings'}&record_document(record).keys()
    with pytest.raises(RuntimeError):
        database=Database();database.read_only=True;BorrowedExposureStore(database,record.card_scope())
    with pytest.raises(policy.ExposureRejected,match='retained_exposure_required'):
        BorrowedExposureStore(Database(),record.card_scope()).save(record)


def test_complete_pending_inventory_refuses_truncation_and_cross_scope():
    scope=policy.CardScope('target','legacy','card')
    with pytest.raises(policy.ExposureRejected,match='complete_exposure_inventory'):
        BorrowedExposureStore(Database([{}]*129),scope).pending_for_card(scope)
    with pytest.raises(policy.ExposureRejected,match='scope_mismatch'):
        BorrowedExposureStore(Database(),scope).pending_for_card(replace(scope,target_id='other'))


def test_ordinary_writer_scope_and_day_are_fenced_before_owner_effects():
    verified,*_=inputs();value=verified.request.encounter;scope=policy.CardScope(value.target_id,value.workspace_id,value.card_id)
    with pytest.raises(policy.ExposureRejected,match='writer_scope'):
        OrdinaryQueueWriter(Database(),scope,'2026-10-20').admit(value,'2026-10-21')


@pytest.fixture
def ordinary(monkeypatch):
    from app.services import review_service,queue_application
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.executescript('''CREATE TABLE cards(id TEXT PRIMARY KEY,revision INTEGER,introduced_at TEXT,state TEXT,
        archived INTEGER DEFAULT 0,pending_validation INTEGER DEFAULT 0,superseded_by TEXT,content_type TEXT);
        CREATE TABLE reviews(card_id TEXT,source_kind TEXT,invalidated_at TEXT);
        CREATE TABLE daily_queue(id INTEGER PRIMARY KEY,queue_date TEXT,card_id TEXT,cycle INTEGER,position INTEGER,
            status TEXT DEFAULT 'queued',attempt_state TEXT,gameplay_priority_reason TEXT,admission_kind TEXT DEFAULT 'new',card_bucket TEXT);
        CREATE TABLE origins(entry_id INTEGER,admission_kind TEXT);
        CREATE TRIGGER origin AFTER INSERT ON daily_queue BEGIN INSERT INTO origins VALUES(NEW.id,NEW.admission_kind); END;
        INSERT INTO cards VALUES('physical',1,'2026-10-18','learning',0,0,NULL,'opening');
        INSERT INTO daily_queue(id,queue_date,card_id,cycle,position,attempt_state,gameplay_priority_reason,admission_kind)
            VALUES(1,'2026-10-20','protected',0,0,'clean','real_game_miss','review');''')
    monkeypatch.setattr(review_service,'capture_daily_queue_order',lambda *args:'verified-order')
    monkeypatch.setattr(review_service,'preserve_daily_queue_order',lambda *args,**kwargs:None)
    def rows(database,day):
        return [dict(id=row['id'],card_id=row['card_id'],position=row['position'],content_type='opening',
            admission_kind=row['admission_kind'],gameplay_priority_reason=row['gameplay_priority_reason'],issued=row['id']==1)
            for row in db.execute('SELECT * FROM daily_queue WHERE queue_date=? ORDER BY position',(day,))]
    monkeypatch.setattr(queue_application,'presentation_rows',rows)
    monkeypatch.setattr(queue_application,'available_attempt_sql',lambda:"SELECT q.id FROM daily_queue q WHERE q.queue_date=? AND q.status='queued'")
    yield db,review_service,queue_application
    db.close()


def test_canonical_ordinary_admission_appends_once_and_preserves_head_history_and_introduction(ordinary):
    db,owner,_=ordinary;before=tuple(db.execute('SELECT * FROM cards').fetchone())
    result=owner.enqueue_introduced_review(db,'physical',1,'2026-10-20')
    assert result.status=='queued' and result.entry_id==2
    assert owner.enqueue_introduced_review(db,'physical',1,'2026-10-20')==result
    assert [tuple(row) for row in db.execute('SELECT card_id,position,gameplay_priority_reason,admission_kind FROM daily_queue ORDER BY position')]==[
        ('protected',0,'real_game_miss','review'),('physical',1,None,'review')]
    assert tuple(db.execute('SELECT * FROM cards').fetchone())==before
    assert db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0]==0
    assert [row[0] for row in db.execute('SELECT admission_kind FROM origins WHERE entry_id=2')]==['review']


@pytest.mark.parametrize('status,reason',[('buried','buried_today'),('complete','already_practiced_today')])
def test_buried_or_complete_card_does_not_get_an_extra_same_day_attempt(ordinary,status,reason):
    db,owner,_=ordinary
    db.execute("INSERT INTO daily_queue(queue_date,card_id,cycle,position,status) VALUES('2026-10-20','physical',0,1,?)",(status,))
    assert owner.enqueue_introduced_review(db,'physical',1,'2026-10-20').reason==reason
    assert db.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id='physical'").fetchone()[0]==1


def test_unsafe_eligibility_rolls_back_new_admission_instead_of_claiming_fallback_success(ordinary,monkeypatch):
    db,owner,projection=ordinary
    monkeypatch.setattr(projection,'available_attempt_sql',lambda:"SELECT q.id FROM daily_queue q WHERE q.queue_date=? AND 1=0")
    result=owner.enqueue_introduced_review(db,'physical',1,'2026-10-20')
    assert result.status=='deferred' and result.reason=='queue_eligibility_unavailable'
    assert db.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id='physical'").fetchone()[0]==0
    assert db.execute('SELECT COUNT(*) FROM origins WHERE entry_id=2').fetchone()[0]==0


def test_database_failure_is_not_converted_to_ordinary_admission_success(ordinary,monkeypatch):
    db,owner,_=ordinary
    def failure(*args,**kwargs):raise sqlite3.OperationalError('controlled failure')
    monkeypatch.setattr(owner,'ensure_card_queued_after',failure)
    with pytest.raises(sqlite3.OperationalError,match='controlled failure'):
        owner.enqueue_introduced_review(db,'physical',1,'2026-10-20')
    assert db.execute('SELECT COUNT(*) FROM daily_queue').fetchone()[0]==1
