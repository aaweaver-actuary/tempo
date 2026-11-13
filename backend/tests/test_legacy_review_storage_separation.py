"""Legacy review policy, lock selection and deferred effects borrow fixed storage."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timezone
import json
from types import SimpleNamespace

import pytest
from app.services import learning_review_adapter as review

DAY = date(2026, 10, 21)
NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("failure", ["policy", "day"])
def test_calendar_capture_is_atomic_after_failed_initialization(monkeypatch, failure):
    calls = []
    def policy():
        calls.append("policy")
        if failure == "policy" and calls.count("policy") == 1:
            raise RuntimeError("policy unavailable")
        return SimpleNamespace(timezone="America/New_York", light_first_interval_days=3)

    original_day = review.learning.review_day_at
    def day(*args):
        calls.append("day")
        if failure == "day" and calls.count("day") == 1:
            raise RuntimeError("day unavailable")
        return original_day(*args)

    monkeypatch.setattr(review.learning, "review_day_at", day)
    adapter = review.LearningReviewAdapter(None, None, observed_at=NOW, policy_provider=policy)
    with pytest.raises(RuntimeError):
        adapter._capture_command_day()
    assert adapter._command_calendar is None
    captured = adapter._capture_command_day()
    assert (captured.observed_at, captured.local_day, captured.policy.timezone) == (
        NOW, DAY, "America/New_York")
    assert adapter._capture_command_day() is captured
    assert calls.count("policy") == 2


def test_calendar_context_preserves_recorded_review_day_and_captured_policy():
    from tempo_learning_review.models import ReviewCommand

    adapter = review.LearningReviewAdapter(
        None, None, observed_at=NOW,
        policy_provider=lambda: SimpleNamespace(timezone="America/New_York", light_first_interval_days=3),
    )
    command = ReviewCommand("card", "correct", False, 4, "2026-10-21T02:00:00Z", None, None, "attempt", "{}")
    context = adapter.context(command)
    assert context.local_day == DAY
    assert context.reviewed_at == datetime(2026, 10, 21, 2, tzinfo=timezone.utc)
    assert context.review_day == date(2026, 10, 20)
    assert adapter.policy() is adapter._capture_command_day().policy


@pytest.mark.parametrize("method", ["save_receipt", "save_position_receipt"])
def test_receipt_insert_uses_validated_facts_and_preserves_payload(monkeypatch, method):
    from tempo_learning_review.models import ReviewResult

    payload = {"persisted": True, "queue_entry_id": 4, "review_id": None,
               "idempotent": True, "unknown": {"original": [3, 2, 1]}}
    result = ReviewResult.from_dict(payload)
    calls = []
    monkeypatch.setattr(review.storage, "insert_receipt", lambda *args: calls.append(args))
    database = object()
    command = SimpleNamespace(attempt_id="original-attempt", card_id="card",
                              outcome="correct", guided=False, recorded_at=None)
    getattr(review.LearningReviewAdapter(database, None), method)(
        command, SimpleNamespace(reviewed_at=NOW), result
    )
    assert calls == [(database, "original-attempt", "card", 4, "correct", 0, None,
                      None, "canonical", None,
                      json.dumps(payload) if method == "save_receipt" else "{}")]


@pytest.mark.parametrize("method", ["save_receipt", "save_position_receipt"])
@pytest.mark.parametrize("changes", [
    {"queue_entry_id": 5}, {"review_id": 9}, {"requeue_entry_id": 8},
    {"idempotent": False}, {"reconciliation": "different"}, {"persisted": False},
])
def test_receipt_insert_rejects_divergent_typed_facts_before_write(monkeypatch, method, changes):
    from tempo_learning_review.models import ReviewResult

    result = ReviewResult.from_dict({"persisted": True, "queue_entry_id": 4,
                                     "review_id": 7, "idempotent": True})
    assert result.current is not None
    forged = replace(result, current=replace(result.current, **changes))
    monkeypatch.setattr(review.storage, "insert_receipt", lambda *_: pytest.fail("unexpected write"))
    command = SimpleNamespace(attempt_id="original", card_id="card", outcome="correct",
                              guided=False, recorded_at=None)
    with pytest.raises(ValueError, match="receipt facts"):
        getattr(review.LearningReviewAdapter(None, None), method)(
            command, SimpleNamespace(reviewed_at=NOW), forged
        )


def test_opaque_original_receipt_is_not_decoded_for_new_insertion(monkeypatch):
    from tempo_learning_review.models import ReviewResult

    original = ReviewResult.from_original(' ["historical", {"extra": 1}] ')
    monkeypatch.setattr(review.storage, "insert_receipt", lambda *_: pytest.fail("unexpected write"))
    with pytest.raises(ValueError, match="current review result"):
        review.LearningReviewAdapter(None, None).save_receipt(None, None, original)
    assert original.payload_json == ' ["historical", {"extra": 1}] '


def test_injected_policy_never_opens_settings_read(monkeypatch):
    monkeypatch.setattr(
        review, "read_connection", lambda: pytest.fail("Injected policy opened storage")
    )
    policy = review.LearningReviewAdapter(
        None,
        None,
        policy_provider=lambda: SimpleNamespace(
            timezone="UTC", light_first_interval_days=3
        ),
    ).policy()
    assert policy.timezone == "UTC" and policy.light_first_interval_days == 3


def test_legacy_policy_keeps_own_read_scope_and_closes_before_mapping(monkeypatch):
    events = []
    database = object()

    @contextmanager
    def connection():
        events.append("read")
        yield database
        events.append("closed")

    monkeypatch.setattr(review, "read_connection", connection)

    def settings(db):
        assert db is database
        events.append("settings")
        return dict(timezone="UTC", light_first_interval_days=3)

    monkeypatch.setattr(review.storage, "policy_settings", settings)
    original = review.learning.ReviewPolicy

    def mapped(*args):
        assert events == ["read", "settings", "closed"]
        return original(*args)

    monkeypatch.setattr(review.learning, "ReviewPolicy", mapped)
    assert review.LearningReviewAdapter(object(), None).policy().timezone == "UTC"


@pytest.mark.parametrize("optional_first", [False, True])
@pytest.mark.parametrize("admitted", [False, True])
def test_review_lock_selection_preserves_sorted_current_and_optional_order(
    monkeypatch, optional_first, admitted
):
    events = []
    current, optional = ("z", "a") if optional_first else ("a", "z")
    monkeypatch.setattr(
        review.storage,
        "lock_current_card",
        lambda _db, key: events.append(("current", key)),
    )
    monkeypatch.setattr(
        review.storage,
        "try_lock_continuation",
        lambda _db, key: events.append(("optional", key)) or admitted,
    )
    assert (
        review._lock_review_cards(None, current, SimpleNamespace(card_id=optional))
        is admitted
    )
    assert events == (
        [("optional", optional), ("current", current)]
        if optional_first
        else [("current", current), ("optional", optional)]
    )


def test_missing_card_stops_before_membership_or_integrity_projection(monkeypatch):
    monkeypatch.setattr(review.storage, "card", lambda *_: None)
    monkeypatch.setattr(
        review.storage,
        "card_owners",
        lambda *_: pytest.fail("Missing card read owners"),
    )
    assert review.LearningReviewAdapter(None, None).card("card") is None


@pytest.mark.parametrize("entry_id", [None, 4])
def test_attempt_selection_maps_original_queue_facts_without_sql_fragments(
    monkeypatch, entry_id
):
    events = []
    row = dict(
        id=4,
        card_id="card",
        queue_date=DAY.isoformat(),
        cycle=1,
        admission_kind="review",
        revision=2,
        attempt_failed=0,
        status="queued",
        review_result_json=None,
    )
    from app.services import study_attempt_guard

    monkeypatch.setattr(
        review.storage, "configured", lambda: events.append("configured") or True
    )
    monkeypatch.setattr(
        review.storage, "attempt_by_id", lambda *_: events.append("identified") or row
    )
    monkeypatch.setattr(
        review.storage, "first_queued_attempt", lambda *_: events.append("first") or row
    )
    monkeypatch.setattr(
        review.storage,
        "current_revision",
        lambda *_: events.append("revision") or {"revision": 2},
    )
    monkeypatch.setattr(
        study_attempt_guard,
        "guard_current_attempt",
        lambda database, attempt_id, card_id, revision: events.append(
            ("guard", database, attempt_id, card_id, revision)
        ),
    )
    result = review.LearningReviewAdapter(None, None).find_attempt(
        SimpleNamespace(card_id="card", queue_entry_id=entry_id),
        SimpleNamespace(local_day=DAY),
    )
    assert result.id == 4 and result.revision == 2 and result.recovered is False
    assert events == [
        "configured",
        "identified" if entry_id else "first",
        "revision",
        ("guard", None, 4, "card", 2),
    ]


@pytest.mark.parametrize("entry_id", [None, 4])
def test_completion_retains_order_capture_write_and_preserve(monkeypatch, entry_id):
    events = []
    monkeypatch.setattr(
        review.storage,
        "queue_date",
        lambda *_: events.append("day") or (DAY.isoformat(),),
    )
    monkeypatch.setattr(
        review,
        "capture_daily_queue_order",
        lambda *_: events.append("capture") or "original",
    )
    monkeypatch.setattr(
        review.storage,
        "complete_identified_attempt",
        lambda *_: events.append("identified"),
    )
    monkeypatch.setattr(
        review.storage, "complete_first_attempt", lambda *_: events.append("first")
    )

    def preserve(*_args, **kwargs):
        assert kwargs["previous"] == "original"
        events.append("preserve")

    monkeypatch.setattr(review, "preserve_daily_queue_order", preserve)
    review.LearningReviewAdapter(None, None).complete_attempt(
        SimpleNamespace(card_id="card", queue_entry_id=entry_id),
        SimpleNamespace(local_day=DAY),
        guided=False,
    )
    assert events == [
        "day",
        "capture",
        "identified" if entry_id else "first",
        "preserve",
    ]


@pytest.mark.parametrize(
    "revision,status,expected_revision,allowed",
    [
        (2, "queued", None, True),
        (3, "queued", 2, False),
        (3, "complete", 2, True),
        (3, "complete", 3, False),
    ],
)
def test_evidence_revision_authority_keeps_current_or_retained_completed_revision(
    monkeypatch, revision, status, expected_revision, allowed
):
    monkeypatch.setattr(review.storage, "current_revision", lambda *_: (revision,))
    monkeypatch.setattr(review.storage, "entry_status", lambda *_: (status,))
    command = SimpleNamespace(
        card_id="card",
        queue_entry_id=4,
        expected_revision=expected_revision,
        completion=SimpleNamespace(card_revision=2),
    )
    assert (
        review.LearningReviewAdapter(None, None).evidence_revision_allowed(command)
        is allowed
    )


def test_postgres_maturity_signals_through_atomic_task_owner(monkeypatch):
    from app.services import durable_tasks

    events = []
    database = object()
    monkeypatch.setattr(review.storage, "configured", lambda: True)

    def enqueue(db, *_args, **_kwargs):
        assert db is database
        events.append("task")

    monkeypatch.setattr(durable_tasks, "enqueue_task_in_transaction", enqueue)
    review.LearningReviewAdapter(database, None).refresh_maturity(
        SimpleNamespace(local_day=DAY)
    )
    # The task owner now writes the epoch/projection atomically; a second mark
    # would race completion in the compatibility path.
    assert events == ["task"]


def test_receipt_write_precedes_late_current_revision_and_remediation_settlement(
    monkeypatch,
):
    from app.services import legacy_study_remediation

    events = []
    monkeypatch.setattr(
        review.storage, "insert_receipt", lambda *_: events.append("receipt")
    )
    monkeypatch.setattr(
        review.storage,
        "current_revision",
        lambda *_: events.append("revision") or dict(revision=2),
    )
    monkeypatch.setattr(
        legacy_study_remediation,
        "settle_review",
        lambda *_args, **kwargs: events.append(("settle", kwargs["revision"])),
    )
    command = SimpleNamespace(
        attempt_id="attempt",
        card_id="card",
        outcome="correct",
        guided=False,
        recorded_at=None,
        queue_entry_id=4,
    )
    review.LearningReviewAdapter(None, None).save_receipt(
        command, SimpleNamespace(reviewed_at=NOW), dict(queue_entry_id=4, review_id=7)
    )
    assert events == ["receipt", "revision", ("settle", 2)]


def test_queue_attempt_projection_keeps_null_admission_and_default_revision():
    from tempo_storage.postgres import TempoRow

    raw = '{ "persisted": true, "unknown": [1, 2] }'
    row = TempoRow(
        ("id", "card_id", "queue_date", "cycle", "admission_kind", "attempt_failed", "status", "review_result_json"),
        (4, "card", DAY.isoformat(), 1, None, 0, "complete", raw),
    )
    result = review.LearningReviewAdapter(None, None)._attempt(row)
    assert result.id == 4 and result.card_id == "card" and result.queue_date == DAY.isoformat()
    assert result.cycle == 1 and result.admission_kind is None and result.revision == 0
    assert result.attempt_failed is False and result.status == "complete" and result.recovered is False
    assert result.review_result_json == raw


def test_queue_attempt_recovery_preserves_original_revision_and_receipt(monkeypatch):
    from app import queue_attempt_origins

    raw = '{"unknown":{"ordered":[3,2,1]},"persisted":true}'
    original = dict(id=9, card_id="card", queue_date=DAY.isoformat(), cycle=2,
                    admission_kind=None, revision=7, attempt_failed=1, status="complete",
                    review_result_json=raw, _recovered=True)
    monkeypatch.setattr(queue_attempt_origins, "recover_queue_entry", lambda *_: original)
    result = review.LearningReviewAdapter(None, None).recover_attempt(SimpleNamespace(card_id="card"))
    assert result.id == 9 and result.card_id == "card" and result.queue_date == DAY.isoformat()
    assert result.cycle == 2 and result.admission_kind is None and result.revision == 7
    assert result.attempt_failed is True and result.status == "complete" and result.recovered is True
    assert result.review_result_json == raw


def test_missing_queue_attempt_remains_none_and_invalid_row_fails_closed(monkeypatch):
    monkeypatch.setattr(review.storage, "first_queued_attempt", lambda *_: None)
    adapter = review.LearningReviewAdapter(None, None)
    assert adapter.find_attempt(SimpleNamespace(card_id="card", queue_entry_id=None), SimpleNamespace(local_day=DAY)) is None
    invalid = dict(id="not-an-id", card_id="card", queue_date=DAY.isoformat(), cycle=0,
                   admission_kind=None, attempt_failed=0, status="queued", review_result_json=None)
    with pytest.raises(ValueError, match="Invalid retained queue attempt facts"):
        adapter._attempt(invalid)


def test_queue_attempt_recovery_preserves_original_conflict(monkeypatch):
    from app import queue_attempt_origins
    from app.contracts.review_conflicts import ReviewConflict

    original = ReviewConflict("queue_attempt_unprovable", "Original attempt is unavailable.")
    def reject(*_args):
        raise original
    monkeypatch.setattr(queue_attempt_origins, "recover_queue_entry", reject)
    with pytest.raises(ReviewConflict) as error:
        review.LearningReviewAdapter(None, None).recover_attempt(SimpleNamespace(card_id="card"))
    assert error.value is original
