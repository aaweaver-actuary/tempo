"""Legacy review policy, lock selection and deferred effects borrow fixed storage."""
from contextlib import contextmanager
from datetime import date,datetime,timezone
from types import SimpleNamespace
import json

import pytest
from app.services import learning_review_adapter as review

DAY=date(2026,10,21);NOW=datetime(2026,10,21,12,tzinfo=timezone.utc)


def test_injected_policy_never_opens_settings_read(monkeypatch):
    monkeypatch.setattr(review,'read_connection',lambda: pytest.fail('Injected policy opened storage'))
    policy=review.LearningReviewAdapter(None,None,policy_provider=lambda: SimpleNamespace(timezone='UTC',light_first_interval_days=3)).policy()
    assert policy.timezone=='UTC' and policy.light_first_interval_days==3


def test_legacy_policy_keeps_own_read_scope_and_closes_before_mapping(monkeypatch):
    events=[];database=object()
    @contextmanager
    def connection():events.append('read');yield database;events.append('closed')
    monkeypatch.setattr(review,'read_connection',connection)
    def settings(db):
        assert db is database;events.append('settings');return dict(timezone='UTC',light_first_interval_days=3)
    monkeypatch.setattr(review.storage,'policy_settings',settings)
    original=review.learning.ReviewPolicy
    def mapped(*args):
        assert events==['read','settings','closed'];return original(*args)
    monkeypatch.setattr(review.learning,'ReviewPolicy',mapped)
    assert review.LearningReviewAdapter(object(),None).policy().timezone=='UTC'


@pytest.mark.parametrize('optional_first',[False,True])
@pytest.mark.parametrize('admitted',[False,True])
def test_review_lock_selection_preserves_sorted_current_and_optional_order(monkeypatch,optional_first,admitted):
    events=[];current,optional=('z','a') if optional_first else ('a','z')
    monkeypatch.setattr(review.storage,'lock_current_card',lambda _db,key: events.append(('current',key)))
    monkeypatch.setattr(review.storage,'try_lock_continuation',lambda _db,key: events.append(('optional',key)) or admitted)
    assert review._lock_review_cards(None,current,SimpleNamespace(card_id=optional)) is admitted
    assert events==([('optional',optional),('current',current)] if optional_first else [('current',current),('optional',optional)])


def test_missing_card_stops_before_membership_or_integrity_projection(monkeypatch):
    monkeypatch.setattr(review.storage,'card',lambda *_: None)
    monkeypatch.setattr(review.storage,'card_owners',lambda *_: pytest.fail('Missing card read owners'))
    assert review.LearningReviewAdapter(None,None).card('card') is None


@pytest.mark.parametrize('entry_id',[None,4])
def test_attempt_selection_maps_original_queue_facts_without_sql_fragments(monkeypatch,entry_id):
    events=[];row=dict(id=4,card_id='card',queue_date=DAY.isoformat(),cycle=1,admission_kind='review',
        revision=2,attempt_failed=0,status='queued',review_result_json=None)
    monkeypatch.setattr(review.storage,'configured',lambda: events.append('configured') or True)
    monkeypatch.setattr(review.storage,'attempt_by_id',lambda *_: events.append('identified') or row)
    monkeypatch.setattr(review.storage,'first_queued_attempt',lambda *_: events.append('first') or row)
    result=review.LearningReviewAdapter(None,None).find_attempt(SimpleNamespace(card_id='card',queue_entry_id=entry_id),SimpleNamespace(local_day=DAY))
    assert result.id==4 and result.revision==2 and result.recovered is False
    assert events==['configured','identified' if entry_id else 'first']


@pytest.mark.parametrize('entry_id',[None,4])
def test_completion_retains_order_capture_write_and_preserve(monkeypatch,entry_id):
    events=[]
    monkeypatch.setattr(review.storage,'queue_date',lambda *_: events.append('day') or (DAY.isoformat(),))
    monkeypatch.setattr(review,'capture_daily_queue_order',lambda *_: events.append('capture') or 'original')
    monkeypatch.setattr(review.storage,'complete_identified_attempt',lambda *_: events.append('identified'))
    monkeypatch.setattr(review.storage,'complete_first_attempt',lambda *_: events.append('first'))
    def preserve(*_args,**kwargs):assert kwargs['previous']=='original';events.append('preserve')
    monkeypatch.setattr(review,'preserve_daily_queue_order',preserve)
    review.LearningReviewAdapter(None,None).complete_attempt(SimpleNamespace(card_id='card',queue_entry_id=entry_id),SimpleNamespace(local_day=DAY),guided=False)
    assert events==['day','capture','identified' if entry_id else 'first','preserve']


@pytest.mark.parametrize('revision,status,expected_revision,allowed',[(2,'queued',None,True),(3,'queued',2,False),(3,'complete',2,True),(3,'complete',3,False)])
def test_evidence_revision_authority_keeps_current_or_retained_completed_revision(monkeypatch,revision,status,expected_revision,allowed):
    monkeypatch.setattr(review.storage,'current_revision',lambda *_: (revision,))
    monkeypatch.setattr(review.storage,'entry_status',lambda *_: (status,))
    command=SimpleNamespace(card_id='card',queue_entry_id=4,expected_revision=expected_revision,completion=SimpleNamespace(card_revision=2))
    assert review.LearningReviewAdapter(None,None).evidence_revision_allowed(command) is allowed


def test_postgres_maturity_enqueues_before_projection_on_same_transaction(monkeypatch):
    from app.services import durable_tasks
    events=[];database=object()
    monkeypatch.setattr(review.storage,'configured',lambda: True)
    def enqueue(db,*_args,**_kwargs):assert db is database;events.append('task')
    def mark(db,day):assert db is database and day==DAY.isoformat();events.append('projection')
    monkeypatch.setattr(durable_tasks,'enqueue_task_in_transaction',enqueue)
    monkeypatch.setattr(review,'mark_queue_refreshing',mark)
    review.LearningReviewAdapter(database,None).refresh_maturity(SimpleNamespace(local_day=DAY))
    assert events==['task','projection']


def test_sqlite_maturity_defers_and_resolves_callback_at_flush(monkeypatch):
    events=[]
    monkeypatch.setattr(review.storage,'configured',lambda: False)
    adapter=review.LearningReviewAdapter(None,None)
    adapter.refresh_maturity(SimpleNamespace(local_day=DAY));assert events==[]
    monkeypatch.setattr(adapter,'_compatibility_queue_refresh',lambda day: events.append(day))
    adapter.finish_compatibility_effects();assert events==[DAY.isoformat()]


def test_receipt_write_precedes_late_current_revision_and_remediation_settlement(monkeypatch):
    from app.services import legacy_study_remediation
    events=[]
    monkeypatch.setattr(review.storage,'insert_receipt',lambda *_: events.append('receipt'))
    monkeypatch.setattr(review.storage,'current_revision',lambda *_: events.append('revision') or dict(revision=2))
    monkeypatch.setattr(legacy_study_remediation,'settle_review',lambda *_args,**kwargs: events.append(('settle',kwargs['revision'])))
    command=SimpleNamespace(attempt_id='attempt',card_id='card',outcome='correct',guided=False,recorded_at=None,queue_entry_id=4)
    review.LearningReviewAdapter(None,None).save_receipt(command,SimpleNamespace(reviewed_at=NOW),dict(queue_entry_id=4,review_id=7))
    assert events==['receipt','revision',('settle',2)]
