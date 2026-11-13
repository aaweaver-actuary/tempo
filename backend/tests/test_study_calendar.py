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
        return reconciliation.FreshReconciliation({"persisted": True, "review_id": None, "queue_entry_id": 4, "requeue_entry_id": None, "reconciliation": "history_only"})
    monkeypatch.setattr(adapter_module, "reconcile_completed_review", reconcile)
    adapter.reconcile_completed(command, SimpleNamespace(id=4), context, attempt_id="attempt", guided=False, revision=2)
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
    assert result.payload["reconciliation"] == "computer_fallback"
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
    saved = reconciliation.reconcile_completed_review(object(),"card",4,**args)
    assert isinstance(saved, reconciliation.RetainedReconciliation)
    assert saved.result_json == prior["result_json"] and json.loads(saved.result_json) == result


def test_retained_reconciliation_rejects_different_original_identity_before_handoff(monkeypatch):
    from fastapi import HTTPException

    prior = dict(card_id="other", queue_entry_id=4, outcome="correct", guided=0,
                 completed_at=None, result_json=' {"old": true} ')
    monkeypatch.setattr(reconciliation.storage, "review_attempt_receipt", lambda *args: prior)
    monkeypatch.setattr(reconciliation, "card_schedule_state", lambda *args: pytest.fail("unexpected schedule read"))
    with pytest.raises(HTTPException) as caught:
        reconciliation.reconcile_completed_review(object(), "card", 4, **arguments())
    assert caught.value.status_code == 409 and prior["result_json"] == ' {"old": true} '


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

@pytest.mark.parametrize("instant,zone,expected", [
    ("2026-11-12T02:00:00+00:00", "America/New_York", "2026-11-11"),
    ("2026-11-12T23:00:00+00:00", "Pacific/Auckland", "2026-11-13"),
])
def test_authoring_split_lock_effect_and_followups_share_one_calendar(monkeypatch, instant, zone, expected):
    from app import prefix_split_commands as command
    from app.study.policy.clock import study_calendar_at
    calendar = study_calendar_at(datetime.fromisoformat(instant), zone)
    database = object()
    events = []
    def capture(db):
        assert db is database
        assert not events, "calendar was recaptured after content effects"
        events.append("capture")
        return calendar
    monkeypatch.setattr(command, "capture_study_calendar", capture)
    monkeypatch.setattr(command, "_lock_prefix_card", lambda *a: None)
    monkeypatch.setattr(command.split_storage, "source_queue_days", lambda *a: [])
    monkeypatch.setattr(command, "lock_queue_date_for_position", lambda db, day: events.append(("lock", day)))
    def split(db, card, revision, *, calendar):
        assert calendar.observed_at == datetime.fromisoformat(instant)
        assert calendar.day.isoformat() == expected
        events.append(("effect", calendar.day.isoformat()))
        return {"idempotent": False, "parent": {"card_id": "parent"}, "continuation": {"card_id": "child"}}
    monkeypatch.setattr(command, "apply_prefix_split", split)
    monkeypatch.setattr(command.storage, "linked_repertoire_rows", lambda *a: [("b",), ("a",)])
    monkeypatch.setattr(command.storage, "mark_split_pending_validation", lambda *a: None)
    monkeypatch.setattr(command, "invalidate_integrity_in_transaction", lambda *a: None)
    monkeypatch.setattr(command, "request_graph_rebuild_in_transaction", lambda db, rep, day: events.append((rep, day)))
    monkeypatch.setattr(command, "_shared_usage", lambda *a: {})
    command.accept_prefix_split(database, {"card_id":"parent", "request":{"expected_revision":1}})
    assert events == ["capture", ("lock", expected), ("effect", expected), ("a", expected), ("b", expected)]


def test_readonly_health_uses_same_captured_day_for_query_and_progress(monkeypatch):
    from contextlib import contextmanager
    from app.operations import postgres_readiness as health
    from app.study.policy.clock import study_calendar_at
    calendar = study_calendar_at(datetime(2026,11,12,2,tzinfo=timezone.utc), "America/New_York")
    database = object()
    calls = []
    @contextmanager
    def reader():
        calls.append("reader")
        yield database
    def capture(db):
        assert db is database
        calls.append("capture")
        return calendar
    def facts(reader, today):
        with reader() as db:
            assert db is database
            assert today() == calendar.day
            assert today() == calendar.day
        return ((health.POSTGRES_SCHEMA_VERSION,), (True,), (1,), None,
            {"state":"leased", "payload_json":json.dumps({"queue_date":calendar.day.isoformat()})})
    monkeypatch.setattr(health, "read_connection", reader)
    monkeypatch.setattr(health, "capture_study_calendar", capture)
    monkeypatch.setattr(health, "read_health_facts", facts)
    monkeypatch.setattr(health.celery_app.control, "inspect", lambda **kw: SimpleNamespace(
        active_queues=lambda: {"worker":[{"name":"foreground"},{"name":"background"}]}))
    assert health.postgres_health()["status"] == "ok"
    assert calls == ["reader", "capture"]


def test_repair_replacement_due_uses_supplied_day_without_reset_policy_expansion(monkeypatch):
    from app.repertoire.changes import retained_mutations as mutation
    from app.services import canonical_prefix
    monkeypatch.setattr(canonical_prefix, "certify_admitted_route", lambda *a: None)
    monkeypatch.setattr(mutation, "card_id", lambda *a: "replacement")
    monkeypatch.setattr(mutation, "card_columns", lambda db: ["id", "due_date"])
    monkeypatch.setattr(mutation.edit_storage, "locked_replacement", lambda *a: None)
    writes = []
    monkeypatch.setattr(mutation.edit_storage, "insert_replacement", lambda db, columns, values: writes.append(values))
    for name in ("mark_canonical", "admit_membership", "supersede"):
        monkeypatch.setattr(mutation.edit_storage, name, lambda *a: None)
    monkeypatch.setattr(mutation.card_storage, "delete_membership", lambda *a: None)
    monkeypatch.setattr(mutation.card_storage, "remaining_membership", lambda *a: [])
    row = {"id":"original", "start_fen":"fen", "due_date":"old", "fsrs_card_json":"retained", "stability":9}
    assert mutation.rewrite_card(object(), "rep", row, ["e2e4"], study_day=date(2026,11,11), validated_route={}, certify=False)
    assert writes[0]["due_date"] == "2026-11-11"
    assert writes[0]["fsrs_card_json"] == "retained" and writes[0]["stability"] == 9
    assert writes[0]["state"] == "new" and writes[0]["introduced_at"] is None


def test_prefix_effect_uses_context_day_and_instant_but_keeps_existing_queue_origin(monkeypatch):
    from app.services import prefix_split as split
    from app.study.policy.clock import study_calendar_at
    calendar = study_calendar_at(datetime(2026,11,12,2,tzinfo=timezone.utc), "America/New_York")
    source = {"id":"source", "revision":1, "repertoire_id":"rep", "canonical_route_source":0, "start_fen":"fen", "moves_json":"[]"}
    detail = {"shortened_card_id":"short", "continuation_card_id":"child", "shortened_moves":["e2e4"], "continuation_starting_fen":"fen2", "continuation_moves":["e7e5"]}
    monkeypatch.setattr(split, "_trained_color_for_card", lambda *a: "white")
    monkeypatch.setattr(split, "_legal_split", lambda *a: detail)
    monkeypatch.setattr(split, "_response", lambda *a, **kw: kw)
    monkeypatch.setattr(split.storage, "prior_split", lambda *a: None)
    monkeypatch.setattr(split.storage, "has_active_issued_attempt", lambda *a: False)
    monkeypatch.setattr(split.storage, "card_row", lambda db, key: source if key=="source" else None)
    monkeypatch.setattr(split.storage, "card_present", lambda *a: False)
    monkeypatch.setattr(split.storage, "membership_rows", lambda *a: [])
    monkeypatch.setattr(split.storage, "source_queue_entries", lambda *a: [{"queue_date":"2026-11-09", "cycle":0, "id":41}])
    origins = []
    monkeypatch.setattr(split.storage, "queue_collision", lambda db, key: origins.append(key) or None)
    writes, days, stamps = [], [], []
    monkeypatch.setattr(split, "_copy_card", lambda db, source, values: writes.append(values))
    monkeypatch.setattr(split, "capture_daily_queue_order", lambda db, day: days.append(day) or "order")
    monkeypatch.setattr(split, "_queue_split_followups", lambda db, a, b, day, **kw: days.append(day))
    monkeypatch.setattr(split.storage, "insert_revision", lambda db, values: stamps.append(values[-1]))
    monkeypatch.setattr(split.storage, "insert_split", lambda db, values: stamps.append(values[-1]))
    for name in ("reassign_queue_entry", "upsert_membership", "archive_source_card", "delete_source_memberships"):
        monkeypatch.setattr(split.storage, name, lambda *a: None)
    split.apply_prefix_split(object(), "source", 1, calendar=calendar)
    assert days == ["2026-11-11"] * 2
    assert stamps == [calendar.observed_at.isoformat()] * 2
    assert writes[1]["due_date"] == writes[1]["introduced_at"] == "2026-11-11"
    assert origins == [("2026-11-09", "short", 0)]

@pytest.mark.parametrize("zone, expected", [
    ("America/New_York", "2026-11-11"),
    ("Pacific/Auckland", "2026-11-12"),
])
def test_settings_followup_uses_effective_saved_zone(monkeypatch, zone, expected):
    from app import settings_commands
    from app.contracts.transport.settings import Settings
    from app.study.policy.clock import study_calendar_at
    instant = datetime(2026, 11, 12, 2, tzinfo=timezone.utc)
    current = Settings(timezone="Pacific/Auckland").model_dump()
    writes, requests = [], []
    monkeypatch.setattr(settings_commands.storage, "locked_settings", lambda _: current)
    monkeypatch.setattr(settings_commands.storage, "store_settings", lambda db, values, columns: writes.append(values))
    def capture(_):
        assert writes, "settings must be saved before selecting the effective zone"
        return study_calendar_at(instant, writes[-1]["timezone"])
    monkeypatch.setattr(settings_commands, "capture_study_calendar", capture)
    monkeypatch.setattr(settings_commands, "request_queue_refresh_in_transaction", lambda db, day: requests.append(day))
    settings_commands.apply_settings(object(), settings_commands.SettingsRequest(Settings(timezone=zone), frozenset({"timezone"})))
    assert requests == [expected]


def test_backward_day_creates_distinct_task_preserving_leased_singleton(monkeypatch):
    from app.services import durable_tasks
    from tempo_storage.queue_refresh import foreground
    original = {"id":"old", "generation":7, "state":"leased", "payload_json":json.dumps({"queue_date":"2026-11-12", "_queue_phase":"admit_due"})}
    created = {"id":"new", "generation":1, "state":"queued", "payload_json":json.dumps({"queue_date":"2026-11-11"})}
    before = dict(original)
    keys, signals, events = [], [], []
    def lock(db, **kwargs):
        key = kwargs.get("deduplication_key", "current")
        keys.append(key)
        return (original, False) if key == "current" else (created, True)
    monkeypatch.setattr(durable_tasks.storage, "lock_or_create_current_queue_task", lock)
    monkeypatch.setattr(durable_tasks.storage, "locked_queue_task_for_day", lambda db, day: None)
    monkeypatch.setattr(foreground, "signal_queue_refresh_in_transaction", lambda db, day: signals.append(day))
    monkeypatch.setattr(durable_tasks, "_record_event", lambda *a, **kw: events.append((a, kw)))
    monkeypatch.setattr(durable_tasks, "mark_queue_refresh_requested", lambda: None)
    result = durable_tasks._request_current_queue_in_transaction(object(), {"queue_date":"2026-11-11"}, priority=10, max_attempts=5, delay_seconds=0, minimum_generation=0)
    assert result == created and original == before
    assert keys == ["current", "current-day:2026-11-11"]
    assert signals == ["2026-11-11"] and len(events) == 1


def test_future_day_slice_defers_without_changing_its_lease_identity(monkeypatch):
    from app.services import postgres_queue_refresh as refresh
    now = datetime(2026, 11, 12, 2, tzinfo=timezone.utc)
    calls = []
    task = {"id":"original", "generation":7, "lease_token":"lease"}
    monkeypatch.setattr(refresh, "_queue_slice_current_day", lambda db: (now, "2026-11-11"))
    monkeypatch.setattr(refresh, "lock_current_slice", lambda db, value: calls.append(("lock", value.copy())) or True)
    monkeypatch.setattr(refresh, "defer_future_queue_slice_in_transaction", lambda db, value, *, now: calls.append(("defer", value.copy(), now)))
    assert not refresh._admit_current_day_slice(object(), task, "2026-11-12")
    assert calls == [("lock", task), ("defer", task, now)]
    calls.clear()
    assert refresh._admit_current_day_slice(object(), task, "2026-11-11")
    assert calls == [("lock", task)]


@pytest.mark.parametrize("payload", [{}, {"_refresh_cycle_epoch": 7, "_queue_phase": "admit_due"}, {"queue_date": "not-a-day"}])
@pytest.mark.parametrize("current_lease", [True, False])
def test_dayless_accepted_slice_holds_original_checkpoint_without_inventing_day(monkeypatch, payload, current_lease):
    from contextlib import contextmanager
    from app.services import postgres_queue_refresh as refresh
    task = {"id": "original", "generation": 7, "lease_token": "lease", "payload": payload.copy()}
    calls = []
    @contextmanager
    def connection(**kwargs):
        assert kwargs == {"background": True}
        yield object()
    monkeypatch.setattr(refresh, "connection", connection)
    monkeypatch.setattr(refresh, "lock_current_slice", lambda db, value: calls.append(("lock", value.copy())) or current_lease)
    monkeypatch.setattr(refresh, "hold_unknown_queue_day_in_transaction", lambda db, value: calls.append(("hold", value.copy())))
    monkeypatch.setattr(refresh, "_queue_slice_current_day", lambda db: pytest.fail("must not infer a day"))
    assert not refresh.execute_postgres_queue_refresh_slice(task)
    assert [call[0] for call in calls] == (["lock", "hold"] if current_lease else ["lock"])
    assert task["payload"] == payload


def test_fresh_day_does_not_replace_unknown_accepted_singleton(monkeypatch):
    from app.services import durable_tasks
    from tempo_storage.queue_refresh import foreground
    old = {"id":"old", "generation":7, "state":"retrying", "payload_json":'{"_queue_phase":"admit_due"}'}
    fresh = {"id":"fresh", "generation":1, "state":"queued", "payload_json":'{"queue_date":"2026-11-11"}'}
    before = dict(old)
    keys, signals = [], []
    def lock(db, **kwargs):
        key = kwargs.get("deduplication_key", "current")
        keys.append(key)
        return (old, False) if key == "current" else (fresh, True)
    monkeypatch.setattr(durable_tasks.storage, "lock_or_create_current_queue_task", lock)
    monkeypatch.setattr(durable_tasks.storage, "locked_queue_task_for_day", lambda db, day: None)
    monkeypatch.setattr(foreground, "signal_queue_refresh_in_transaction", lambda db, day: signals.append(day))
    monkeypatch.setattr(durable_tasks, "_record_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(durable_tasks, "mark_queue_refresh_requested", lambda: None)
    task = durable_tasks._request_current_queue_in_transaction(object(), {"queue_date":"2026-11-11"}, priority=10, max_attempts=5, delay_seconds=0, minimum_generation=0)
    assert task == fresh and old == before
    assert keys == ["current", "current-day:2026-11-11"] and signals == ["2026-11-11"]
