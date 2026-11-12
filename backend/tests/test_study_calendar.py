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


def test_composition_captures_one_clock_and_settings_read(monkeypatch):
    from app.services import study_calendar
    calls=[]
    observed=datetime(2026,11,12,2,tzinfo=timezone.utc)
    class Clock:
        @staticmethod
        def now(zone):
            calls.append("clock")
            return observed
    monkeypatch.setattr(study_calendar,"datetime",Clock)
    monkeypatch.setattr(study_calendar,"study_timezone",lambda db:calls.append("zone") or "America/New_York")
    result=study_calendar.capture_study_calendar(object())
    assert (result.observed_at,result.day)==(observed,date(2026,11,11))
    assert calls==["clock","zone"]


def test_tactic_attempt_lock_due_and_queue_share_capture_and_replay_retains_receipt(monkeypatch):
    from app import tactic_commands as command, card_deletion
    from app.study.policy.clock import study_calendar_at
    import chess
    calendar=study_calendar_at(datetime(2026,11,12,2,tzinfo=timezone.utc),"America/New_York")
    calls=[]
    monkeypatch.setattr(command,"capture_study_calendar",lambda db:calendar)
    monkeypatch.setattr(card_deletion,"require_card_not_deleted",lambda *args:None)
    monkeypatch.setattr(command,"lock_daily_tactic_admission",lambda db,day:calls.append(("daily",day)))
    monkeypatch.setattr(command,"lock_queue_date_for_position",lambda db,day:calls.append(("queue-lock",day)))
    storage=command.storage
    monkeypatch.setattr(storage,"lock_puzzle_attempt",lambda *args:None)
    monkeypatch.setattr(storage,"attempt_receipt",lambda *args:None)
    monkeypatch.setattr(storage,"first_light_interval",lambda *args:7)
    monkeypatch.setattr(storage,"ensure_tactic_repertoire",lambda db,instant:calls.append(("instant",instant)))
    monkeypatch.setattr(storage,"save_progress",lambda *args:None)
    monkeypatch.setattr(storage,"create_attempt_card",lambda *args:calls.append(("due",args[4],args[-1])))
    monkeypatch.setattr(storage,"lock_attempt_card",lambda *args:None)
    monkeypatch.setattr(storage,"reopen_attempt_card",lambda db,day,card:calls.append(("reopen",day)))
    monkeypatch.setattr(storage,"queued_attempt_card",lambda *args:None)
    monkeypatch.setattr(command,"ensure_card_queued_after",lambda *args,**kw:calls.append(("place",kw["queue_date"])))
    monkeypatch.setattr(storage,"attempt_card_schedule",lambda *args:("normal","2026-11-11"))
    monkeypatch.setattr(storage,"save_admission_mode",lambda *args:None)
    monkeypatch.setattr(storage,"save_attempt_receipt",lambda *args:None)
    payload=dict(request=dict(puzzle_id="p",deck_id="d",correct=False,clean=False,attempt_id="attempt",source_fen=chess.STARTING_FEN,moves=["d2d4"]),
        pack_id="pack",training_fen=chess.STARTING_FEN,solution=["e2e4"])
    result=command.submit_tactic_attempt(object(),payload)
    assert calls==[("daily","2026-11-11"),("instant",calendar.observed_at.isoformat()),
        ("due","2026-11-11","2026-11-11"),("reopen","2026-11-11"),
        ("queue-lock","2026-11-11"),("place","2026-11-11")]
    calls.clear()
    monkeypatch.setattr(storage,"attempt_receipt",lambda *args:(json.dumps(result),))
    assert command.submit_tactic_attempt(object(),payload)==result
    assert calls==[("daily","2026-11-11")]


def test_authored_completion_uses_capture_but_keeps_issued_queue_origin(monkeypatch):
    from app.services import study_attempts, queue_application
    from app.study.policy.clock import study_calendar_at
    calendar=study_calendar_at(datetime(2026,11,12,2,tzinfo=timezone.utc),"America/New_York")
    calls=[]
    storage=study_attempts.storage
    monkeypatch.setattr(storage,"active_review_card",lambda *args:dict(id="card",revision=1))
    monkeypatch.setattr(storage,"queued_review_entry",lambda *args:dict(id=4,attempt_failed=0,queue_date="2026-11-09"))
    monkeypatch.setattr(storage,"light_interval_setting",lambda *args:(7,))
    def schedule(*args,**kwargs):
        calls.append(("schedule",kwargs["reviewed_at"],kwargs["review_day"]))
        return dict(requeue_today=True,requeue_after_cards=4)
    monkeypatch.setattr(study_attempts,"apply_scheduling_review",schedule)
    monkeypatch.setattr(queue_application,"requeue",lambda db,day,*args:calls.append(("origin",day)) or 5)
    monkeypatch.setattr(storage,"complete_queue_entry",lambda *args:None)
    monkeypatch.setattr(storage,"latest_review_id",lambda *args:7)
    monkeypatch.setattr(storage,"finalize_attempt",lambda db,instant,*args:calls.append(("finalized",instant)))
    monkeypatch.setattr(storage,"retain_queue_result",lambda *args:None)
    monkeypatch.setattr(storage,"exercise",lambda *args:dict(study_id="s",id="e",position_id="p",sibling_group="g"))
    monkeypatch.setattr(storage,"sibling_exercises",lambda *args:[("sibling",)])
    monkeypatch.setattr(storage,"bury_sibling",lambda db,sibling,day,reason:calls.append(("bury",day)))
    monkeypatch.setattr(storage,"block_sibling_queue",lambda db,day,sibling:calls.append(("block",day)))
    attempt=dict(id="a",context="review",card_id="card",exercise_id="e",queue_entry_id=4,cycle=0,
        revision=1,hint_seen=False,solution_seen_before_answer=False,assessment_json="{}")
    study_attempts.finish_study_attempt(object(),attempt,"correct",calendar=calendar)
    assert calls==[("schedule",calendar.observed_at,calendar.day),("origin","2026-11-09"),
        ("finalized",calendar.observed_at.isoformat()),("bury","2026-11-11"),("block","2026-11-11")]


@pytest.mark.parametrize("explicit",[None,"2020-01-02"])
def test_new_task_acceptance_captures_missing_day_but_never_recaptures_explicit_day(monkeypatch,explicit):
    from app.services import durable_tasks, study_calendar
    from app.study.policy.clock import study_calendar_at
    calendar=study_calendar_at(datetime(2026,11,12,2,tzinfo=timezone.utc),"America/New_York")
    captures=[]
    monkeypatch.setattr(study_calendar,"capture_study_calendar",lambda db,**kwargs:captures.append(True) or calendar)
    class Captured(Exception):pass
    payload={} if explicit is None else {"queue_date":explicit}
    def persist(db,**kwargs):
        assert json.loads(kwargs["payload_json"])["queue_date"]==(explicit or "2026-11-11")
        raise Captured
    monkeypatch.setattr(durable_tasks.storage,"lock_or_create_current_queue_task",persist)
    with pytest.raises(Captured):
        durable_tasks._request_current_queue_in_transaction(object(),payload,priority=0,max_attempts=3,delay_seconds=0,minimum_generation=0)
    assert captures==([True] if explicit is None else [])
    assert payload==({} if explicit is None else {"queue_date":explicit})


def test_capture_command_passes_same_calendar_to_admission_and_refresh(monkeypatch):
    from app import tactic_commands
    from app.study.policy.clock import study_calendar_at
    import chess
    import uuid
    calendar=study_calendar_at(datetime(2026,11,12,2,tzinfo=timezone.utc),"America/New_York")
    calls=[]
    monkeypatch.setattr(tactic_commands,"capture_study_calendar",lambda db:calls.append("capture") or calendar)
    def capture(db,request,*,calendar):
        calls.append(calendar)
        return {"capture_id":str(request.capture_id)}
    monkeypatch.setattr(tactic_commands,"capture_tactic",capture)
    monkeypatch.setattr(tactic_commands,"request_queue_refresh_in_transaction",lambda db,day:calls.append(day))
    tactic_commands.create_tactic_capture(object(),{"request":dict(capture_id=str(uuid.uuid4()),starting_fen=chess.STARTING_FEN,moves=["e2e4"])})
    assert calls==["capture",calendar,"2026-11-11"]


def test_issued_origin_day_wins_over_new_current_day(monkeypatch):
    from app import queue_commands, queue_attempt_origins
    storage=queue_commands.queue_storage
    reads=[]
    monkeypatch.setattr(storage,"prepare_available_attempt",lambda *args:lambda day:reads.append(day) or {"id":4})
    monkeypatch.setattr(storage,"issued_origin_day",lambda *args:"2020-01-02")
    monkeypatch.setattr(storage,"prepare_queue_head",lambda *args:lambda day:reads.append(day) or {"id":4})
    monkeypatch.setattr(storage,"issued_origin",lambda *args:True)
    monkeypatch.setattr(queue_attempt_origins,"validate_failure_marker",lambda *args:None)
    def no_capture(*args):
        raise AssertionError("issued replay recaptured a day")
    monkeypatch.setattr(queue_commands,"current_study_day",no_capture)
    queue_commands.validate_available_attempt(object(),4,"card",1,current_day="2026-11-11")
    assert reads==["2020-01-02","2020-01-02"]
