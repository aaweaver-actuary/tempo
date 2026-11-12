"""Explicit Study conversion and captured-context reconciliation, without services."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
import json
import os
import subprocess
import sys

import pytest
import tempo_learning_review as learning
from app.services import learning_review_adapter as adapter_module
from app.study.policy import review_reconciliation as reconciliation


@pytest.mark.parametrize("instant,zone,day", [
    ("2026-11-12T02:00:00+00:00", "America/New_York", "2026-11-11"),
    ("2026-11-12T23:00:00+00:00", "Pacific/Auckland", "2026-11-13"),
    ("2026-03-08T06:59:59+00:00", "America/New_York", "2026-03-08"),
    ("2026-03-08T07:00:00+00:00", "America/New_York", "2026-03-08"),
    ("2026-11-01T05:59:59+00:00", "America/New_York", "2026-11-01"),
    ("2026-11-01T06:00:00+00:00", "America/New_York", "2026-11-01"),
    ("2026-11-12T01:00:00+09:00", "America/New_York", "2026-11-11"),
])
def test_reconciliation_uses_same_explicit_conversion(instant, zone, day):
    moment = datetime.fromisoformat(instant)
    assert reconciliation._review_day is learning.review_day_at
    assert reconciliation._review_day(moment, zone) == date.fromisoformat(day)


def test_valid_zone_independent_of_host_and_local_fallback_preserved_in_child_processes():
    code = '''
import json,time
from datetime import datetime
from tempo_learning_review import review_day_at
time.tzset()
instant=datetime.fromisoformat("2026-11-12T02:00:00+00:00")
print(json.dumps([review_day_at(instant,z).isoformat() for z in
    ["America/New_York","Pacific/Auckland","local","Missing/Zone"]]))
'''
    # Only subprocess environment changes; never alter the machine's timezone.
    values = [json.loads(subprocess.check_output([sys.executable, "-c", code],
        env={**os.environ, "TZ": host}, text=True)) for host in ["UTC", "America/Los_Angeles"]]
    assert values[0][:2] == values[1][:2] == ["2026-11-11", "2026-11-12"]
    assert values[0][2:] == ["2026-11-12", "2026-11-12"]
    assert values[1][2:] == ["2026-11-11", "2026-11-11"]


def test_adapter_captures_once_and_reconciliation_reuses_clock_zone_and_day(monkeypatch):
    observed = datetime(2026,11,12,2,tzinfo=timezone.utc)
    calls = []
    def policy():
        calls.append(True)
        assert len(calls) == 1, "command reread changed policy"
        return SimpleNamespace(timezone="America/New_York", light_first_interval_days=7)
    adapter = adapter_module.LearningReviewAdapter(object(), None, policy_provider=policy, observed_at=observed)
    command = SimpleNamespace(recorded_at="2026-11-10T23:00:00-05:00", card_id="card", attempt_id="attempt", outcome="correct")
    context = adapter.context(command)
    assert context.local_day == date(2026,11,11)
    assert context.review_day == date(2026,11,10)
    assert context.reviewed_at == datetime(2026,11,11,4,tzinfo=timezone.utc)
    assert adapter.context(command) == context
    assert adapter.policy().timezone == "America/New_York"
    received = {}
    def reconcile(*args, **kwargs):
        received.update(kwargs)
        return {"persisted":True}
    monkeypatch.setattr(adapter_module, "reconcile_completed_review", reconcile)
    adapter.reconcile_completed(command, SimpleNamespace(id=4), context, guided=False, revision=2)
    assert received["observed_at"] == observed
    assert received["local_day"] == context.local_day
    assert received["completed_at"] == context.reviewed_at
    assert received["timezone_name"] == "America/New_York"
    assert len(calls) == 1


def arguments(**updates):
    return dict(attempt_id="attempt", outcome="correct", guided=False, completed_at=None,
        expected_revision=2, timezone_name="America/New_York",
        observed_at=datetime(2026,11,12,2,tzinfo=timezone.utc), local_day=date(2026,11,11), **updates)


def test_reconciliation_without_recorded_events_uses_captured_fallback(monkeypatch):
    storage = reconciliation.storage
    current = {"due_date":"2026-12-01", "interval_days":10}
    monkeypatch.setattr(storage,"review_attempt_receipt",lambda *args:None)
    monkeypatch.setattr(reconciliation,"card_schedule_state",lambda *args:current)
    monkeypatch.setattr(storage,"card_revision",lambda *args:(2,))
    monkeypatch.setattr(storage,"ordered_valid_reviews",lambda *args:[])
    inserted, due, unlocked, receipts = [], [], [], []
    monkeypatch.setattr(storage,"insert_independent_review",lambda db,**kw:inserted.append(kw))
    monkeypatch.setattr(storage,"independent_review_id",lambda *args:5)
    monkeypatch.setattr(storage,"set_conservative_due",lambda db,day,card:due.append(day))
    monkeypatch.setattr(reconciliation,"unlock_legacy_children_after_review",lambda db,card,day,state:unlocked.append((day,state)))
    monkeypatch.setattr(storage,"retain_attempt_receipt",lambda db,**kw:receipts.append(kw))
    result = reconciliation.reconcile_completed_review(object(),"card",4,**arguments())
    assert result["reconciliation"] == "computer_fallback"
    assert inserted[0]["reviewed_at"] == "2026-11-12T02:00:00+00:00"
    assert due == ["2026-11-12"]
    assert unlocked == [("2026-11-11","learning")]
    assert receipts[0]["completed_at"] is None


def test_original_reconciliation_receipt_returns_without_calendar_conversion(monkeypatch):
    result={"persisted":True,"review_id":77,"queue_entry_id":4,"warning":"original"}
    moment=datetime(2020,1,2,3,tzinfo=timezone.utc)
    prior=dict(card_id="card",queue_entry_id=4,outcome="correct",guided=0,
        completed_at=moment.isoformat(),result_json=json.dumps(result))
    monkeypatch.setattr(reconciliation.storage,"review_attempt_receipt",lambda *args:prior)
    def unexpected(*args,**kwargs):
        raise AssertionError("replay touched policy/current state")
    monkeypatch.setattr(reconciliation,"_review_day",unexpected)
    monkeypatch.setattr(reconciliation,"card_schedule_state",unexpected)
    args=arguments();args["completed_at"]=moment
    assert reconciliation.reconcile_completed_review(object(),"card",4,**args)==result
