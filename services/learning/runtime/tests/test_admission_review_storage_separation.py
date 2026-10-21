"""Admission/review authority stays in application decisions over named facts."""
from datetime import date
from types import SimpleNamespace

import pytest

from tempo_learning_runtime import admission, review_adapter as review


DAY = "2026-10-21"


def card():
    return dict(id="card", serving_active=1, archived=0, superseded_by=None, pending_validation=0,
        due_date=DAY, state="new", content_type="tactic", repertoire_id="rep", introduced_at=None,
        revision=2, publication_id="pub", publication_generation=1)


@pytest.mark.parametrize("rejection", ["inactive", "deleted"])
def test_inactive_or_deleted_admission_stops_before_family_or_quota_reads(monkeypatch, rejection):
    facts = card()
    facts["serving_active"] = int(rejection != "inactive")
    events = []
    monkeypatch.setattr(admission.storage, "card", lambda *_: events.append("card") or facts)
    monkeypatch.setattr(admission.storage, "deleted_card", lambda *_: events.append("deletion") or (1,))
    monkeypatch.setattr(admission.storage, "admission_settings", lambda *_: pytest.fail("Rejected card read quota"))
    assert admission.admit_prelocked(None, "card", DAY) is False
    assert events == (["card"] if rejection == "inactive" else ["card", "deletion"])


@pytest.mark.parametrize("used", [1, 2])
def test_new_tactic_uses_tactic_quota_and_inserts_before_introduction(monkeypatch, used):
    events = []
    monkeypatch.setattr(admission.storage, "card", lambda *_: card())
    monkeypatch.setattr(admission.storage, "deleted_card", lambda *_: None)
    monkeypatch.setattr(admission.storage, "existing_entry", lambda *_: None)
    monkeypatch.setattr(admission.storage, "admission_settings", lambda *_: dict(
        study_new_per_day=8, tactic_new_per_day=2, new_cards_per_day=10))
    def quota(_db, day, family):
        assert (day, family) == (DAY, "tactic")
        return used
    monkeypatch.setattr(admission.storage, "used_family_quota", quota)
    monkeypatch.setattr(admission.storage, "next_position", lambda *_: events.append("position") or 7)
    def insert(_db, day, identifier, position, family, kind, membership, reason):
        assert (day, identifier, position, family, kind, membership, reason) == (DAY, "card", 7, "tactic", "new", "rep", None)
        events.append("insert")
    monkeypatch.setattr(admission.storage, "insert_entry", insert)
    monkeypatch.setattr(admission.storage, "introduce_card", lambda *_: events.append("introduce"))
    assert admission.admit_prelocked(None, "card", DAY) is (used < 2)
    assert events == (["position", "insert", "introduce"] if used < 2 else [])


def test_miss_proposal_definition_fence_precedes_route_and_queue_reads(monkeypatch):
    monkeypatch.setattr(admission.storage, "card", lambda *_: card())
    monkeypatch.setattr(admission.storage, "deleted_card", lambda *_: None)
    monkeypatch.setattr(admission, "has_outstanding_real_game_miss", lambda *_: True)
    monkeypatch.setattr(admission.storage, "buried_entry", lambda *_: pytest.fail("Changed proposal reached queue"))
    proposal = SimpleNamespace(card_revision=2, publication_id="pub", publication_generation=2)
    assert admission.admit_prelocked(None, "card", DAY, admission.MISS_REASON, proposal=proposal) is False


@pytest.mark.parametrize("protected", [False, True])
def test_miss_promotion_retains_protected_attempt_and_original_queue_capability(monkeypatch, protected):
    from tempo_learning_runtime import review_service
    facts = card()
    facts["state"] = "locked"
    events = []
    monkeypatch.setattr(admission.storage, "card", lambda *_: facts)
    monkeypatch.setattr(admission.storage, "deleted_card", lambda *_: None)
    monkeypatch.setattr(admission, "has_outstanding_real_game_miss", lambda *_: True)
    monkeypatch.setattr(admission.storage, "buried_entry", lambda *_: None)
    monkeypatch.setattr(admission, "protected_queue_ids", lambda *_: {4})
    monkeypatch.setattr(admission.storage, "queued_entry_ids", lambda *_: [(4,)] if protected else [])
    def ensure(_db, identifier, **options):
        assert identifier == "card"
        assert options == dict(after_cards=0, attempt_state="gameplay", priority_reason=admission.MISS_REASON,
            queue_date=DAY, protected_ids={4}, preserve_order=False)
        events.append("promote")
    monkeypatch.setattr(review_service, "ensure_card_queued_after", ensure)
    monkeypatch.setattr(admission.storage, "retain_admission_membership", lambda *_: events.append("membership"))
    monkeypatch.setattr(admission.storage, "introduce_missed_card", lambda *_: events.append("introduce"))
    monkeypatch.setattr(admission.storage, "admission_settings", lambda *_: pytest.fail("Miss promotion consumed new-card quota"))
    assert admission.admit_prelocked(None, "card", DAY, admission.MISS_REASON) is (not protected)
    assert events == ([] if protected else ["promote", "membership", "introduce"])


def test_opening_frozen_proposal_rejection_precedes_quota_and_write(monkeypatch):
    facts = card()
    facts["content_type"] = "opening"
    monkeypatch.setattr(admission.storage, "card", lambda *_: facts)
    monkeypatch.setattr(admission.storage, "deleted_card", lambda *_: None)
    monkeypatch.setattr(admission.storage, "opening_route", lambda *_: ("route",))
    monkeypatch.setattr(admission.frozen_opening_order, "annotate", lambda *_: [dict(card_id="card")])
    monkeypatch.setattr(admission.frozen_opening_order, "proposal_valid", lambda *_: False)
    monkeypatch.setattr(admission.storage, "existing_entry", lambda *_: pytest.fail("Stale frozen proposal reached queue"))
    assert admission.admit_prelocked(None, "card", DAY) is False


@pytest.mark.parametrize("current,optional", [("a", "z"), ("z", "a")])
def test_denied_optional_lock_keeps_sorted_current_card_lock_and_false_result(monkeypatch, current, optional):
    events = []
    monkeypatch.setattr(review.storage, "lock_current_review_card", lambda _db, identifier: events.append(("current", identifier)))
    monkeypatch.setattr(review.storage, "try_lock_continuation_card", lambda _db, identifier: events.append(("optional", identifier)) or False)
    assert review._lock_review_cards(None, current, SimpleNamespace(card_id=optional)) is False
    assert events == sorted([("current", current), ("optional", optional)], key=lambda event: event[1])


@pytest.mark.parametrize("archived", [False, True])
def test_review_card_fact_keeps_serving_read_short_circuit(monkeypatch, archived):
    events = []
    monkeypatch.setattr(review.storage, "card", lambda *_: dict(content_type="opening", archived=int(archived), superseded_by=None, revision=2, state="learning"))
    monkeypatch.setattr(review.storage, "card_owners", lambda *_: events.append("owners") or ("rep",))
    monkeypatch.setattr(review.storage, "card_integrity", lambda *_: events.append("integrity") or (1,))
    monkeypatch.setattr(review.storage, "serving_active", lambda *_: events.append("serving") or 1)
    facts = review.LearningReviewAdapter(None, None).card("card")
    assert facts.archived is archived and facts.owner_ids == ("rep",)
    assert events == ["owners", "integrity"] + ([] if archived else ["serving"])


@pytest.mark.parametrize("revision,status,allowed", [(2, "queued", True), (3, "complete", True), (3, "queued", False)])
def test_completion_evidence_retains_current_or_previously_issued_revision_authority(monkeypatch, revision, status, allowed):
    monkeypatch.setattr(review.storage, "evidence_revision_facts", lambda *_: ((revision,), (status,)))
    command = SimpleNamespace(card_id="card", queue_entry_id=4, expected_revision=2,
        completion=SimpleNamespace(card_revision=2))
    assert review.LearningReviewAdapter(None, None).evidence_revision_allowed(command) is allowed


@pytest.mark.parametrize("identified", [False, True])
def test_completion_keeps_original_day_capture_before_write_then_preserve(monkeypatch, identified):
    events = []
    monkeypatch.setattr(review.storage, "queue_date", lambda *_: ("2026-10-20",) if identified else None)
    monkeypatch.setattr(review, "capture_daily_queue_order", lambda _db, day: events.append(("capture", day)) or "previous")
    monkeypatch.setattr(review.storage, "complete_identified_attempt", lambda *_: events.append("identified"))
    monkeypatch.setattr(review.storage, "complete_first_queued_attempt", lambda *_: events.append("legacy"))
    monkeypatch.setattr(review, "preserve_daily_queue_order", lambda _db, day, **options: events.append(("preserve", day, options["previous"])))
    review.LearningReviewAdapter(None, None).complete_attempt(SimpleNamespace(queue_entry_id=4 if identified else None, card_id="card"),
        SimpleNamespace(local_day=date(2026, 10, 21)), guided=True)
    day = "2026-10-20" if identified else DAY
    assert events == [("capture", day), "identified" if identified else "legacy", ("preserve", day, "previous")]


def test_origin_only_recovery_retains_completion_without_rewriting_daily_attempt(monkeypatch):
    from tempo_learning_runtime import queue_attempt_origins
    monkeypatch.setattr(review.storage, "retain_daily_completion", lambda *_: pytest.fail("Origin-only completion rewrote queue"))
    calls = []
    monkeypatch.setattr(queue_attempt_origins, "retain_recovered_completion", lambda _db, *args: calls.append(args))
    review.LearningReviewAdapter(None, None).retain_completion(SimpleNamespace(card_id="card"),
        SimpleNamespace(id=4, card_id="card", revision=2, recovered=True), "result", origin_only=True)
    assert calls == [(dict(id=4, card_id="card", revision=2), "result")]
