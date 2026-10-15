"""Execute the installed orchestration over bounded, observable capabilities."""
from dataclasses import replace
from datetime import date, datetime, timezone
import json

import pytest
import tempo_learning_review as review

NOW = datetime(2026, 10, 15, 2, 30, tzinfo=timezone.utc)
DAY = date(2026, 10, 14)
CONTEXT = review.ReviewContext(NOW, DAY, DAY)


def command(**changes):
    payload = dict(outcome="correct", guided=False, queue_entry_id=1, recorded_at=None,
                   expected_review_id=None, expected_revision=1, attempt_id="attempt-a")
    payload.update(changes)
    prepared = payload.pop("prepared_next", None)
    completion = payload.pop("completion", None)
    wire = {**payload, **({"prepared_next": prepared.as_dict()} if prepared else {})}
    return review.ReviewCommand("card-a", **payload, payload_json=json.dumps(wire),
                                prepared_next=prepared, completion=completion)


class Capabilities:
    def __init__(self):
        self.calls = []
        self.deleted = False
        self.saved_receipt = None
        self.card_facts = review.CardFacts("opening", False, None, 1, "learning", True, ("repertoire",))
        self.attempt = review.QueueAttempt(1, "card-a", DAY.isoformat(), 0, "new", 1, False, "queued", None)
        self.recovered = replace(self.attempt, recovered=True)
        self.continuation = review.ContinuationFacts(0, 1, "opening", False)
        self.later = False
        self.locked = True
        self.issue = True
        self.evidence_exists = True
        self.evidence_revision = True
        self.failure = None
        self.scheduled = dict(card_id="card-a", next_due=DAY.isoformat(), interval_days=0,
            state="learning", requeue_today=True, requeue_after_cards=4, stability=2.3,
            scheduling_mode="normal", hard_correct_streak=0, suggest_shorter_prefix=False, idempotent=False)

    def event(self, name, *values):
        self.calls.append((name, *values))
        if self.failure == name:
            raise RuntimeError(name)

    def is_deleted(self, card_id): return self.deleted
    def receipt(self, attempt_id): return self.saved_receipt
    def card(self, card_id): return self.card_facts
    def find_attempt(self, cmd, ctx): return self.attempt
    def recover_attempt(self, cmd):
        self.event("recover")
        return self.recovered
    def current_revision(self, card_id): return self.card_facts.revision
    def latest_review_id(self, card_id, *, valid_only): return 0 if valid_only else 12
    def policy(self): return review.ReviewPolicy("America/New_York", 7)
    def retain_introduction(self, *args): self.event("introduction", *args)
    def has_later_review(self, *args): return self.later
    def reconcile_completed(self, cmd, attempt, ctx, **options):
        self.event("reconcile", options)
        return dict(persisted=True, review_id=13, queue_entry_id=attempt.id,
                    requeue_entry_id=None, reconciliation="chronological")
    def bind_receipt(self, cmd, request_json, *, restore_original=False):
        self.event("bind", restore_original)
        if self.saved_receipt:
            self.saved_receipt = replace(self.saved_receipt, request_json=request_json)
    def schedule_snapshot(self, card_id): return {"interval_days":0}
    def schedule(self, cmd, ctx, **options):
        self.event("schedule", options["outcome"], options["guided"], ctx.reviewed_at, ctx.review_day)
        return self.scheduled.copy()
    def complete_attempt(self, cmd, ctx, **options): self.event("complete", options["guided"])
    def mark_clean_tactic(self, *args): self.event("clean")
    def requeue(self, *args):
        self.event("requeue", args[-1])
        return 2
    def save_snapshot(self, *args): self.event("snapshot")
    def save_receipt(self, cmd, ctx, result):
        self.event("receipt")
        self.saved_receipt = review.AttemptReceipt(cmd.card_id, result["queue_entry_id"], cmd.outcome,
            cmd.guided, ctx.reviewed_at.isoformat() if cmd.recorded_at else None, json.dumps(result), None)
    def retain_completion(self, cmd, attempt, result_json, **options): self.event("retain", options)
    def refresh_progression(self, *args): self.event("progression")
    def refresh_priority(self, repertoire, **options): self.event("priority", options)
    def refresh_maturity(self, *args): self.event("maturity")
    def lock_review_cards(self, cmd):
        self.event("cards_lock")
        return self.locked
    def lock_queue_date(self): self.event("queue_lock")
    def completed_evidence_exists(self, *args): return self.evidence_exists
    def evidence_revision_allowed(self, *args): return self.evidence_revision
    def persist_evidence(self, *args): self.event("evidence")
    def complete_evidence(self, *args): self.event("complete_evidence")
    def inherit_evidence_scope(self, *args): self.event("inherit")
    def continuation_facts(self, *args): return self.continuation
    def issue_head(self, *args):
        self.event("issue")
        return self.issue
    def save_continuation(self, attempt_id, result):
        self.event("continuation")
        self.saved_receipt = replace(self.saved_receipt, result_json=json.dumps(result))


def test_ordinary_review_sequence_and_original_receipt_replay():
    store = Capabilities()
    result = review.apply_review(store, command(), CONTEXT).as_dict()
    assert result == {**store.scheduled, "queue_entry_id":1, "requeue_entry_id":2,
                      "review_id":12, "persisted":True, "idempotent":False}
    assert [call[0] for call in store.calls] == ["recover", "schedule", "complete", "clean", "requeue",
        "snapshot", "receipt", "bind", "retain", "progression", "priority"]
    before = list(store.calls)
    store.card_facts = replace(store.card_facts, archived=True)
    assert review.apply_review(store, command(), CONTEXT).as_dict() == result
    assert store.calls == before
    store.deleted = True
    with pytest.raises(review.ReviewConflict, match="permanently deleted"):
        review.apply_review(store, command(), CONTEXT)


@pytest.mark.parametrize("changes", [{"outcome":"again"}, {"guided":True},
    {"queue_entry_id":3}, {"expected_revision":2}, {"recorded_at":NOW.isoformat()},
    {"prepared_next":review.PreparedNext(2,"card-b",1,0,DAY.isoformat())}])
def test_logical_receipt_rejects_changed_original_payload(changes):
    store = Capabilities()
    review.apply_review(store, command(), CONTEXT)
    with pytest.raises(review.ReviewConflict) as caught:
        review.apply_review(store, command(**changes), CONTEXT)
    assert caught.value.code == "review_attempt_reused"


@pytest.mark.parametrize("field,value,code", [("archived",True,"card_archived"),
    ("superseded_by","replacement","card_replaced"), ("content_type","defense","review_requires_rubric"),
    ("integrity_allowed",False,"card_integrity_blocked")])
def test_unavailable_card_distinctions(field, value, code):
    store = Capabilities()
    store.card_facts = replace(store.card_facts, **{field:value})
    with pytest.raises(review.ReviewConflict) as caught:
        review.apply_review(store, command(), CONTEXT)
    assert caught.value.code == code
    assert not store.calls


@pytest.mark.parametrize("failure", ["schedule","complete","receipt","retain","progression"])
def test_required_effect_failure_propagates_to_transaction_owner(failure):
    store = Capabilities()
    store.failure = failure
    with pytest.raises(RuntimeError, match=failure):
        review.submit_review(store, command(), lambda: CONTEXT)
    assert not any(call[0] == "issue" for call in store.calls)


@pytest.mark.parametrize("guided,failed", [(True,False),(False,True),(True,True)])
def test_guided_failure_schedules_again_but_binds_original_request(guided, failed):
    store = Capabilities()
    store.attempt = replace(store.attempt, attempt_failed=failed)
    review.apply_review(store, command(guided=guided), CONTEXT)
    assert ("schedule", "again", True, NOW, DAY) in store.calls
    assert store.saved_receipt.outcome == "correct" and store.saved_receipt.guided == guided
    assert ("clean",) not in store.calls


def test_completed_projection_legacy_confirmation_and_broader_replay():
    store = Capabilities()
    stored = {**store.scheduled, "review_id":9, "_request":{"outcome":"correct","guided":False},
              "_recorded_at":NOW.isoformat()}
    store.attempt = replace(store.attempt, status="complete", review_result_json=json.dumps(stored))
    result = review.apply_review(store, command(recorded_at=NOW.isoformat()), CONTEXT).as_dict()
    assert result["idempotent"] and store.saved_receipt
    store.saved_receipt = None
    result = review.apply_review(store, command(attempt_id=None), CONTEXT).as_dict()
    assert result["idempotent"] and result["review_id"] == 9


def test_completed_attempt_competitor_and_recovered_chronological_path():
    store = Capabilities()
    store.attempt = replace(store.attempt, status="complete", review_result_json=json.dumps({
        "_request":{"outcome":"correct"},"_attempt_id":"other","_recorded_at":NOW.isoformat()}))
    result = review.apply_review(store, command(), CONTEXT).as_dict()
    assert result["reconciliation"] == "chronological"
    assert store.calls[0][0] == "reconcile" and store.calls[-2][0] == "priority"
    store = Capabilities()
    store.attempt = None
    result = review.apply_review(store, command(), CONTEXT).as_dict()
    assert result["reconciliation"] == "chronological"
    assert ("retain", {"origin_only":True}) in store.calls
    assert ("bind", True) in store.calls


@pytest.mark.parametrize("recorded,code", [("invalid",422),("2026-01-01T00:00:00",422),
    ("2026-10-15T03:00:00Z",422)])
def test_timestamp_validation(recorded, code):
    with pytest.raises(review.ReviewFailure) as caught:
        review.review_context(command(recorded_at=recorded), observed_at=NOW, local_day=DAY, timezone_name="local")
    assert caught.value.status_code == code


def test_offline_clock_normalization_and_scheduling_local_day():
    ctx = review.review_context(command(recorded_at="2026-10-14T22:30:00-04:00"),
        observed_at=NOW, local_day=DAY, timezone_name="America/New_York")
    assert ctx.reviewed_at == NOW and ctx.review_day == DAY and ctx.local_day == DAY
    store = Capabilities()
    review.apply_review(store, command(recorded_at="2026-10-14T22:30:00-04:00"), ctx)
    assert json.loads(store.saved_receipt.request_json)["recorded_at"] == NOW.isoformat()


def completion():
    return review.EvidenceCompletion("attempt-a","card-a",1,True,1,"{}")


def test_evidence_and_prepared_continuation_sequence_and_exact_replay():
    store = Capabilities()
    cmd = command(completion=completion(), prepared_next=review.PreparedNext(2,"card-b",1,0,DAY.isoformat()))
    result = review.submit_review(store, cmd, lambda: CONTEXT).as_dict()
    names = [call[0] for call in store.calls]
    assert names[:3] == ["cards_lock","queue_lock","evidence"]
    assert names.index("evidence") < names.index("schedule") < names.index("complete_evidence") < names.index("inherit") < names.index("issue")
    assert result["prepared_continuation"]["card_id"] == "card-b"
    assert review.submit_review(store, cmd, lambda: CONTEXT).as_dict() == result
    assert sum(call[0] == "issue" for call in store.calls) == 1


@pytest.mark.parametrize("change", ["day","cycle","revision","defense","study_exercise","absent","lock","admission"])
def test_unavailable_optional_continuation_preserves_saved_review(change):
    store = Capabilities()
    candidate = review.PreparedNext(2,"card-b",1,0,DAY.isoformat())
    if change == "day": candidate = replace(candidate,local_date="1999-01-01")
    elif change == "cycle": store.continuation = replace(store.continuation,cycle=1)
    elif change == "revision": store.continuation = replace(store.continuation,revision=2)
    elif change in {"defense","study_exercise"}: store.continuation = replace(store.continuation,content_type=change)
    elif change == "absent": store.continuation = None
    elif change == "lock": store.locked = False
    elif change == "admission": store.issue = False
    result = review.submit_review(store,command(prepared_next=candidate),lambda:CONTEXT).as_dict()
    assert result["persisted"] and "prepared_continuation" not in result
    assert sum(call[0] == "schedule" for call in store.calls) == 1


@pytest.mark.parametrize("changes", [{"attempt_id":"other"},{"card_id":"other"},
    {"queue_entry_id":None},{"complete":False}])
def test_required_evidence_binding_rejects_mismatches_before_writes(changes):
    store = Capabilities()
    with pytest.raises(review.ReviewFailure) as caught:
        review.submit_review(store, command(completion=replace(completion(),**changes)), lambda:CONTEXT)
    assert caught.value.code == "opening_evidence_conflict"
    assert [call[0] for call in store.calls] == ["cards_lock","queue_lock"]


def test_explicit_registration_and_immutable_internal_records():
    from dataclasses import FrozenInstanceError
    registered = {}
    submit, reconcile = object(), object()
    review.register_review_commands(lambda name, handler: registered.setdefault(name,handler), submit=submit,reconcile=reconcile)
    assert registered == {"cards.review":submit,"cards.review.reconcile":reconcile}
    with pytest.raises(FrozenInstanceError): command().outcome = "again"
    result = review.ReviewResult.from_dict({"values":[1]})
    result.as_dict()["values"].append(2)
    assert result.as_dict() == {"values":[1]}


def test_recorded_receipt_replay_does_not_read_current_scheduling_policy():
    store = Capabilities()
    cmd = command(recorded_at=NOW.isoformat())
    result = review.apply_review(store,cmd,CONTEXT)
    def unavailable(): raise AssertionError("Replay read current policy")
    store.policy = unavailable
    assert review.apply_review(store,cmd,CONTEXT) == result


@pytest.mark.parametrize("timezone_name", ["local","Unavailable/Zone"])
def test_offline_local_timezone_and_unknown_zone_preserve_system_fallback(timezone_name):
    ctx = review.review_context(command(recorded_at=NOW.isoformat()),observed_at=NOW,
                                local_day=DAY,timezone_name=timezone_name)
    assert ctx.review_day == NOW.astimezone().date()
