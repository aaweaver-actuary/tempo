"""Infrastructure-free scheduling decisions and explicit persistence ordering."""
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
import subprocess
import sys

import pytest

from app.services import scheduling_review_application as application
from app.services.scheduler import schedule_review


class MemorySchedulingStore:
    def __init__(self, *, source_exists=False, card=None, maturity=None):
        self.source_exists = source_exists
        self.card = card or application.CardState('2026-01-01',0,None,None,False,0,False,
                                                 'learning','normal',0,'[]')
        self.maturity = maturity or application.MaturityFacts(1,('correct',),None)
        self.calls = []
        self.reviews = []
        self.saved = None

    def source_review_exists(self, source_kind, source_ref):
        self.calls.append(('source', source_kind, source_ref))
        return self.source_exists

    def load_card_state(self, card_id, *, require_active):
        self.calls.append(('load', card_id, require_active))
        return self.card

    def append_review(self, review):
        self.calls.append(('append', review.card_id))
        self.reviews.append(review)

    def load_maturity_facts(self, card_id):
        self.calls.append(('maturity', card_id))
        return self.maturity

    def save_card_state(self, card_id, state):
        self.calls.append(('save', card_id))
        self.saved = state


def apply_review(store, **changes):
    inputs = dict(guided=True,source_kind='study',source_ref='original',
                  light_first_interval_days=7,
                  reviewed_at=datetime(2026,1,2,2,30,tzinfo=timezone.utc),
                  review_day=date(2026,1,1))
    inputs.update(changes)
    return application.apply_scheduling_review(store,'card','correct',**inputs)


def test_scheduling_application_uses_original_completion_clock_without_database():
    store = MemorySchedulingStore()
    result = apply_review(store)
    assert store.calls == [('source','study','original'),('load','card',True),
                           ('append','card'),('maturity','card'),('save','card')]
    assert store.reviews == [application.ReviewRecord('card','correct','good',True,
        datetime(2026,1,2,2,30,tzinfo=timezone.utc),0,0,'study','original')]
    assert result == dict(card_id='card',next_due='2026-01-01',interval_days=0,
        state='learning',requeue_today=True,requeue_after_cards=None,stability=2.3065,
        scheduling_mode='normal',hard_correct_streak=0,suggest_shorter_prefix=False,idempotent=False)
    assert store.saved.first_correct_at == '2026-01-02T02:30:00+00:00'
    assert store.saved.guided_review and store.saved.due_date == '2026-01-01'


def test_scheduling_application_source_replay_preserves_current_result_contract():
    store = MemorySchedulingStore(source_exists=True)
    store.card = replace(store.card,due_date='2030-02-03',interval_days=88,
                         state='mature',stability=42,scheduling_mode='light',
                         hard_correct_streak=3,recent_attempts_json='malformed')
    assert apply_review(store) == dict(card_id='card',next_due='2030-02-03',interval_days=88,
        state='mature',requeue_today=True,requeue_after_cards=4,stability=42,
        scheduling_mode='light',hard_correct_streak=3,suggest_shorter_prefix=False,idempotent=True)
    assert store.calls == [('source','study','original'),('load','card',False)]
    assert not store.reviews and store.saved is None


@pytest.mark.parametrize('facts,expected', [
    (application.MaturityFacts(1,('correct',),application.OpeningSeedFacts(2,1)), 'mature'),
    (application.MaturityFacts(1,('correct',),None), 'learning'),
    (application.MaturityFacts(3,('again','correct'),application.OpeningSeedFacts(5,2)), 'learning'),
    (application.MaturityFacts(3,(),application.OpeningSeedFacts(0,5)), 'mature'),
    (application.MaturityFacts(3,('correct',),application.OpeningSeedFacts(0,0)), 'learning'),
])
def test_scheduling_application_maturity_uses_valid_reviews_and_seed_facts(monkeypatch,facts,expected):
    schedule = replace(schedule_review('correct',reviewed_at=datetime(2026,1,2,tzinfo=timezone.utc),
                                       review_day=date(2026,1,2)),stability=20)
    monkeypatch.setattr(application,'schedule_review',lambda *args,**kwargs: schedule)
    store = MemorySchedulingStore(maturity=facts)
    assert apply_review(store)['state'] == expected
    assert store.saved.state == expected


@pytest.mark.parametrize('source_ref', [None,''])
def test_scheduling_application_empty_source_does_not_lookup_replay(source_ref):
    store = MemorySchedulingStore(source_exists=True)
    assert not apply_review(store,source_ref=source_ref)['idempotent']
    assert store.calls[0] == ('load','card',True)
    assert store.reviews[0].source_ref == source_ref


@pytest.mark.parametrize('stage', ['append_review','load_maturity_facts','save_card_state'])
def test_scheduling_application_propagates_persistence_failure_without_more_effects(stage):
    store = MemorySchedulingStore()
    def fail(*args):
        raise RuntimeError('owned transaction must roll back')
    setattr(store,stage,fail)
    with pytest.raises(RuntimeError,match='owned transaction'):
        apply_review(store)
    assert store.saved is None


def test_scheduling_application_import_does_not_load_infrastructure():
    # A fresh interpreter observes transitive imports, including deferred loader
    # attempts, while performing a complete scheduling application call.
    backend = Path(__file__).resolve().parents[1]
    source = """
import sys
from importlib.abc import MetaPathFinder
class RejectInfrastructure(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'app.main','app.database','app.postgres_store','app.tasks','app.celery_app'} or fullname.split('.')[0] in {'psycopg','redis','celery','fastapi'}:
            raise AssertionError(fullname)
sys.meta_path.insert(0, RejectInfrastructure())
from datetime import date, datetime, timezone
from app.services.scheduling_review_application import SchedulingReviewStore, CardState, MaturityFacts, apply_scheduling_review
class Store:
    def load_card_state(self, card_id, *, require_active):
        return CardState('2026-01-01', 0, None, None, False, 0, False, 'learning', 'normal', 0, '[]')
    def append_review(self, review): pass
    def load_maturity_facts(self, card_id): return MaturityFacts(1, ('correct',), None)
    def save_card_state(self, card_id, state): pass
assert not apply_scheduling_review(Store(), 'card', 'correct', guided=False, source_kind='study', source_ref=None, light_first_interval_days=7, reviewed_at=datetime(2026,1,1,tzinfo=timezone.utc), review_day=date(2026,1,1))['idempotent']
assert set(SchedulingReviewStore.__dict__) & {'commit','rollback','connection','close'} == set()
"""
    completed = subprocess.run([sys.executable,'-c',source],cwd=backend,
                               capture_output=True,text=True,timeout=15)
    assert completed.returncode == 0, completed.stderr
