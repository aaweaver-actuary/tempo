"""Review application decisions consume named facts and retain atomic effects."""
from datetime import date, datetime, timezone
import json
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from tempo_learning_runtime import review_service as service, review_reconciliation as reconciliation


DAY = date(2026, 10, 21)
MOMENT = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


class FixedDate(date):
    @classmethod
    def today(cls):
        return DAY


class FixedDatetime(datetime):
    @classmethod
    def now(cls, _zone=None):
        return MOMENT


def state():
    return dict(due_date="2026-11-01", interval_days=2, fsrs_card_json=None, first_correct_at=None,
                reinforcement_pending=0, stability=1, guided_review=0, state="learning",
                scheduling_mode="normal", hard_correct_streak=0, recent_attempts_json="[]")


def test_empty_defense_history_resets_on_application_day(monkeypatch):
    monkeypatch.setattr(service, "date", FixedDate)
    monkeypatch.setattr(service.storage, "valid_defense_reviews", lambda *_: [])
    writes = []
    monkeypatch.setattr(service.storage, "reset_defense_schedule", lambda _db, *args: writes.append(args))
    monkeypatch.setattr(service, "schedule_review", lambda *_args, **_kw: pytest.fail("Empty history replayed a review"))
    service.rebuild_defense_schedule(None, "card", 3)
    assert writes == [("2026-10-21", "card")]


def test_defense_replay_carries_prior_state_and_unlock_inputs(monkeypatch):
    rows = [dict(rating="correct", reviewed_at="2026-10-19T12:00:00+00:00"),
            dict(rating="again", reviewed_at="2026-10-20T12:00:00+00:00")]
    monkeypatch.setattr(service.storage, "valid_defense_reviews", lambda *_: rows)
    calls = []
    def schedule(rating, **inputs):
        calls.append((rating, inputs["interval_days"], inputs["recent_attempts"], inputs["review_day"]))
        return SimpleNamespace(due_date=DAY, interval_days=len(calls), fsrs_card_json="{}", first_correct_at=None,
            reinforcement_pending=False, stability=10, scheduling_mode="normal", hard_correct_streak=1,
            recent_attempts=[rating])
    monkeypatch.setattr(service, "schedule_review", schedule)
    def unlock(stability, successful_days, recent):
        assert (stability, successful_days, recent) == (10, 1, ["again", "correct"])
        return True
    monkeypatch.setattr(service, "unlock_ready", unlock)
    writes = []
    monkeypatch.setattr(service.storage, "write_defense_schedule", lambda _db, **fields: writes.append(fields))
    service.rebuild_defense_schedule(None, "card", 3)
    assert calls == [("correct", 0, [], date(2026, 10, 19)), ("again", 1, ["correct"], date(2026, 10, 20))]
    assert (writes[0]["state"], writes[0]["interval_days"], writes[0]["recent_attempts_json"]) == ("mature", 2, '["again"]')


@pytest.mark.parametrize("idempotent,source", [(True, "study"), (False, "study"), (False, "study_exercise")])
def test_scheduling_settlement_and_followup_identity_keep_original_order(monkeypatch, idempotent, source):
    from tempo_learning_runtime import scheduling_review_application, receivers
    events = []
    callback = object()
    result = dict(idempotent=idempotent, state="learning")
    monkeypatch.setattr(scheduling_review_application, "apply_scheduling_review", lambda *_args, **_kw: events.append("schedule") or result)
    monkeypatch.setattr(service.storage, "latest_review_id", lambda *_: events.append("latest review") or 7)
    def settle(_db, card, review, **context):
        assert (card, review) == ("card", 7)
        assert context == dict(queue_entry_id=4, revision=2, request_followup=callback)
        events.append("settle")
    monkeypatch.setattr(receivers, "settle", settle)
    monkeypatch.setattr(service, "unlock_legacy_children_after_review", lambda *_: events.append("unlock"))
    assert service.apply_scheduling_review(None, "card", "correct", guided=False, source_kind=source,
        source_ref="source", light_first_interval_days=3, reviewed_at=MOMENT, review_day=DAY,
        queue_entry_id=4, expected_revision=2, request_followup=callback) is result
    assert events == (["schedule"] if idempotent else ["schedule", "latest review"] + (["settle"] if source == "study" else []) + ["unlock"])


@pytest.mark.parametrize("conflict", [False, True])
def test_reconciliation_receipt_replays_or_conflicts_before_current_schedule(monkeypatch, conflict):
    monkeypatch.setattr(reconciliation.storage, "review_attempt_receipt", lambda *_: dict(
        card_id="card", queue_entry_id=4, outcome="again" if conflict else "correct", guided=0,
        completed_at=MOMENT.isoformat(), result_json='{"retained":true}'))
    monkeypatch.setattr(reconciliation, "card_schedule_state", lambda *_: pytest.fail("Receipt read current state"))
    options = dict(attempt_id="attempt", outcome="correct", guided=False, completed_at=MOMENT,
                   expected_revision=2, timezone_name="UTC")
    if conflict:
        with pytest.raises(HTTPException) as error:
            reconciliation.reconcile_completed_review(None, "card", 4, **options)
        assert error.value.status_code == 409
    else:
        assert reconciliation.reconcile_completed_review(None, "card", 4, **options) == dict(retained=True)


def test_changed_revision_saves_history_only_without_schedule_effects(monkeypatch):
    monkeypatch.setattr(reconciliation.storage, "review_attempt_receipt", lambda *_: None)
    monkeypatch.setattr(reconciliation, "card_schedule_state", lambda *_: state())
    monkeypatch.setattr(reconciliation.storage, "card_revision", lambda *_: (3,))
    monkeypatch.setattr(reconciliation.storage, "ordered_valid_reviews", lambda *_: pytest.fail("Changed card read review history"))
    receipts = []
    monkeypatch.setattr(reconciliation.storage, "retain_attempt_receipt", lambda _db, **fields: receipts.append(fields))
    result = reconciliation.reconcile_completed_review(None, "card", 4, attempt_id="attempt", outcome="correct",
        guided=False, completed_at=MOMENT, expected_revision=2, timezone_name="UTC")
    assert result["reconciliation"] == "history_only" and result["review_id"] is None
    assert json.loads(receipts[0]["result_json"]) == result


@pytest.mark.parametrize("later", [False, True])
def test_chronological_replay_snapshots_before_each_event_then_retains_receipt(monkeypatch, later):
    events = []
    monkeypatch.setattr(reconciliation.storage, "review_attempt_receipt", lambda *_: None)
    monkeypatch.setattr(reconciliation, "card_schedule_state", lambda *_: state())
    monkeypatch.setattr(reconciliation.storage, "card_revision", lambda *_: (2,))
    monkeypatch.setattr(reconciliation.storage, "ordered_valid_reviews", lambda *_: [dict(
        id=8, rating="again", guided=0, reviewed_at="2026-10-22T12:00:00+00:00")] if later else [])
    monkeypatch.setattr(reconciliation.storage, "review_snapshot", lambda *_: (json.dumps(state()),))
    monkeypatch.setattr(reconciliation.storage, "insert_independent_review", lambda _db, **fields: events.append(("insert", fields["rating"])))
    monkeypatch.setattr(reconciliation.storage, "independent_review_id", lambda *_: 7)
    monkeypatch.setattr(reconciliation, "save_schedule_snapshot", lambda _db, review, _state: events.append(("snapshot", review)))
    def next_state(_db, _card, current, outcome, guided, moment, review, zone):
        assert zone == "UTC"
        events.append(("schedule", review, outcome, guided))
        return {**current, "interval_days": current["interval_days"] + 1}, current["interval_days"] + 1
    monkeypatch.setattr(reconciliation, "_next_state", next_state)
    monkeypatch.setattr(reconciliation.storage, "update_review_intervals", lambda _db, before, after, review: events.append(("intervals", review, before, after)))
    monkeypatch.setattr(reconciliation, "_write_card_state", lambda *_: events.append("card state"))
    monkeypatch.setattr(reconciliation, "unlock_legacy_children_after_review", lambda *_: events.append("unlock"))
    monkeypatch.setattr(reconciliation.storage, "retain_attempt_receipt", lambda _db, **_fields: events.append("receipt"))
    result = reconciliation.reconcile_completed_review(None, "card", 4, attempt_id="attempt", outcome="correct",
        guided=True, completed_at=MOMENT, expected_revision=2, timezone_name="UTC")
    assert result["reconciliation"] == "chronological"
    assert events == [("insert", "again"), ("snapshot", 7), ("schedule", 7, "again", True), ("intervals", 7, 2, 3)] + (
        [("snapshot", 8), ("schedule", 8, "again", False), ("intervals", 8, 3, 4)] if later else []) + ["card state", "unlock", "receipt"]


def test_missing_completion_time_keeps_computer_schedule_with_near_term_due(monkeypatch):
    monkeypatch.setattr(reconciliation, "date", FixedDate)
    monkeypatch.setattr(reconciliation, "datetime", FixedDatetime)
    monkeypatch.setattr(reconciliation.storage, "review_attempt_receipt", lambda *_: None)
    monkeypatch.setattr(reconciliation, "card_schedule_state", lambda *_: state())
    monkeypatch.setattr(reconciliation.storage, "card_revision", lambda *_: (2,))
    monkeypatch.setattr(reconciliation.storage, "ordered_valid_reviews", lambda *_: [])
    recorded = []
    monkeypatch.setattr(reconciliation.storage, "insert_independent_review", lambda _db, **fields: recorded.append(fields))
    monkeypatch.setattr(reconciliation.storage, "independent_review_id", lambda *_: 7)
    monkeypatch.setattr(reconciliation, "_next_state", lambda *_: pytest.fail("Fallback rescheduled history"))
    due = []
    monkeypatch.setattr(reconciliation.storage, "set_conservative_due", lambda _db, *args: due.append(args))
    monkeypatch.setattr(reconciliation, "unlock_legacy_children_after_review", lambda *_: None)
    monkeypatch.setattr(reconciliation.storage, "retain_attempt_receipt", lambda _db, **_fields: None)
    result = reconciliation.reconcile_completed_review(None, "card", 4, attempt_id="attempt", outcome="correct",
        guided=False, completed_at=None, expected_revision=2, timezone_name="UTC")
    assert recorded[0]["reviewed_at"] == MOMENT.isoformat()
    assert due == [("2026-10-22", "card")] and result["reconciliation"] == "computer_fallback"


def test_next_state_keeps_time_cutoff_and_seeded_success_history(monkeypatch):
    monkeypatch.setattr(reconciliation.storage, "light_interval_setting", lambda *_: (3,))
    def schedule(_outcome, **inputs):
        assert inputs["review_day"] == date(2026, 10, 21) and inputs["light_first_interval_days"] == 3
        return SimpleNamespace(due_date=DAY, interval_days=3, fsrs_card_json=None, first_correct_at=None,
            reinforcement_pending=False, stability=10, scheduling_mode="normal", hard_correct_streak=0, recent_attempts=[])
    monkeypatch.setattr(reconciliation, "schedule_review", schedule)
    monkeypatch.setattr(reconciliation.storage, "valid_review_history", lambda *_: [
        dict(id=6, rating="correct", reviewed_at="2026-10-20T12:00:00+00:00"),
        dict(id=8, rating="again", reviewed_at=MOMENT.isoformat())])
    monkeypatch.setattr(reconciliation.storage, "baseline_schedule_seed", lambda *_: dict(baseline_successful_days=2, baseline_recent_clean=2))
    def unlock(stability, days, recent):
        assert (stability, days, recent) == (10, 3, ["correct", "correct"])
        return True
    monkeypatch.setattr(reconciliation, "unlock_ready", unlock)
    result, interval = reconciliation._next_state(None, "card", state(), "correct", False, MOMENT, 7, "UTC")
    assert (result["state"], interval) == ("mature", 3)
