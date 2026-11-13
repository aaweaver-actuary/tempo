"""Adversarial review orchestration; native transaction proof is separate."""

import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import pytest
import tempo_learning_review as review
from tempo_learning_review.models import ScheduledReview

_path = (
    Path(__file__).resolve().parents[2]
    / "services/learning/application/tests/test_application.py"
)
_spec = importlib.util.spec_from_file_location(
    "position_reconciliation_capabilities", _path
)
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)


class PositionStore(_fixture.Capabilities):
    def begin_position_review(self, command):
        return self.saved_receipt is None

    def lock_position_attempt(self, command):
        self.event("position_lock")

    def record_position_review(self, command, context, *, outcome, guided):
        self.event("human_review")
        return ScheduledReview.from_dict({**self.scheduled, "requeue_today": False, "requeue_after_cards": None})

    def save_position_receipt(self, command, context, result):
        self.save_receipt(command, context, result)

    def apply_position_effects(self, command, context, result):
        self.event("position_effects")

    def finish_position_receipt(self, command, context, result):
        self.event("finish_receipt")


def names(store):
    return [call[0] for call in store.calls]


def test_original_receipt_replay_and_changed_completion_clock_conflict():
    store = PositionStore()
    command = _fixture.command(
        completion=_fixture.completion(), recorded_at=_fixture.NOW.isoformat()
    )
    first = review.submit_review(store, command, lambda: _fixture.CONTEXT).as_dict()
    assert (
        review.submit_review(store, command, lambda: _fixture.CONTEXT).as_dict()
        == first
    )
    assert (
        names(store).count("human_review")
        == names(store).count("position_effects")
        == 1
    )
    assert not {"schedule", "snapshot", "requeue"} & set(names(store))
    later = replace(command, recorded_at="2026-10-15T02:31:00+00:00")
    later = replace(
        later,
        payload_json=json.dumps(
            {**json.loads(command.payload_json), "recorded_at": later.recorded_at}
        ),
    )
    with pytest.raises(review.ReviewConflict) as caught:
        review.submit_review(
            store,
            later,
            lambda: replace(
                _fixture.CONTEXT, reviewed_at=_fixture.NOW.replace(minute=31)
            ),
        )
    assert caught.value.code == "review_attempt_reused"
    assert names(store).count("position_effects") == 1


def test_competing_attempt_cannot_reconcile_a_completed_position_as_card_review():
    store = PositionStore()
    store.attempt = replace(
        store.attempt,
        status="complete",
        review_result_json=json.dumps(
            {
                "_request": {"outcome": "correct", "guided": False},
                "_attempt_id": "other-tab",
                "_recorded_at": _fixture.NOW.isoformat(),
            }
        ),
    )
    with pytest.raises(review.ReviewConflict) as caught:
        review.apply_review(
            store,
            _fixture.command(recorded_at=_fixture.NOW.isoformat()),
            _fixture.CONTEXT,
            position=True,
        )
    assert caught.value.code == "position_attempt_conflict"
    assert not {
        "schedule",
        "snapshot",
        "reconcile",
        "human_review",
        "position_effects",
    } & set(names(store))


def test_recovered_position_attempt_does_not_chronologically_rewrite_physical_schedule():
    store = PositionStore()
    store.attempt = None
    store.later = True
    command = _fixture.command(
        completion=_fixture.completion(), recorded_at=_fixture.NOW.isoformat()
    )
    result = review.submit_review(store, command, lambda: _fixture.CONTEXT).as_dict()
    assert result["reconciliation"] == "queue_origin"
    assert (
        names(store).count("human_review")
        == names(store).count("position_effects")
        == 1
    )
    assert not {"schedule", "snapshot", "reconcile", "requeue"} & set(names(store))


def test_position_completion_requires_evidence_before_any_review_effect():
    store = PositionStore()
    with pytest.raises(review.ReviewFailure) as caught:
        review.submit_review(store, _fixture.command(), lambda: _fixture.CONTEXT)
    assert caught.value.code == "opening_evidence_conflict"
    assert not {"schedule", "human_review", "position_effects", "receipt"} & set(
        names(store)
    )


def test_issued_manifest_accepts_fen_en_passant_target_without_legal_capture():
    """An issued e4 e5 route must remain reviewable with full-FEN e6."""
    from app.study.position_checkpoint import issued_position_manifest
    from tests.test_position_issued_plan_contracts import (
        option,
        request_for,
        select_and_freeze,
    )

    route = option("italian-knight-attack")
    decisions = list(route.decisions)
    assert " KQkq - " in decisions[1].fen
    decisions[1] = replace(
        decisions[1], fen=decisions[1].fen.replace(" KQkq - ", " KQkq e6 ")
    )
    plan = select_and_freeze(
        request_for(replace(route, decisions=tuple(decisions)), target_ply=2)
    )
    manifest = issued_position_manifest(
        plan,
        {
            "manifest_version": 1,
            "policy_version": 1,
            "presentation_snapshot_id": 1,
            "repertoire_id": "rep-italian",
            "card_id": "legacy-card-1",
            "card_revision": 4,
            "trained_color": "white",
        },
    )
    assert manifest["decisions"][1]["fen"] == decisions[1].fen


def test_repeated_position_in_one_completion_has_one_good(monkeypatch):
    """Two clean occurrences share one position/day even in one writer batch."""
    import importlib.util
    from types import SimpleNamespace
    from app.study.position_learning import grade_effects as writer

    path = (
        Path(__file__).resolve().parents[2]
        / "database/tests/position_study/test_position_grade_effects.py"
    )
    spec = importlib.util.spec_from_file_location("position_grade_fixture", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    evidence, due, _, _, state_json = fixture.facts("e2e4", complete=True)
    one = evidence.first_response
    two = replace(one, index=1, event_id="event-2")
    plan = SimpleNamespace(
        plan_id="plan",
        attempt_id=one.attempt_id,
        owner_epoch="epoch",
        target=fixture.DEF,
        due_positions=(due,),
        source_digest="original",
    )
    monkeypatch.setattr(writer, "_load_plan", lambda *_: plan)
    monkeypatch.setattr(
        writer,
        "_load_events",
        lambda *_: (
            (one, "expected", "digest-1", fixture.NOW),
            (two, "expected", "digest-2", fixture.NOW),
        ),
    )
    monkeypatch.setattr(
        writer,
        "_lock_states",
        lambda *_, **__: {
            fixture.KEY: {
                "expected_uci": "e2e4",
                "policy_version": "v1",
                "owner_epoch": "epoch",
                "source_digest": "authored",
                "fsrs_json": state_json,
                "fsrs_internal_card_id": 100,
                "version": 1,
            }
        },
    )
    monkeypatch.setattr(writer, "_completion_saved", lambda *_: True)

    class Cursor:
        rowcount = 1

        def fetchall(self):
            return []

    class Database:
        read_only = False
        raw = SimpleNamespace(
            info=SimpleNamespace(transaction_status=SimpleNamespace(name="INTRANS"))
        )

        def execute_native(self, *_):
            return Cursor()

    result = writer.apply_in_borrowed_transaction(
        Database(),
        plan.attempt_id,
        (one.event_id, two.event_id),
        owner_epoch="epoch",
        authored_digest="authored",
        accepted_study_day=fixture.DAY,
        accepted_at=fixture.NOW,
        completion=object(),
    )
    assert [r.grade for r in result] == ["good", "observe"]


def test_production_adapter_translates_owner_schedule_and_reconciliation_without_changing_payload(monkeypatch):
    from contextlib import nullcontext
    from app.services import learning_review_adapter as adapter_module
    from tempo_learning_review.models import ScheduleSnapshot, ReconciledReview

    adapter = object.__new__(adapter_module.LearningReviewAdapter)
    adapter.database = object()
    adapter._command_timezone = 'America/New_York'
    adapter._command_time = _fixture.NOW
    owner_result = _fixture.Capabilities().scheduled
    reconciliation = {'persisted': True, 'review_id': None, 'queue_entry_id': 1,
        'requeue_entry_id': None, 'reconciliation': 'history_only', 'warning': 'retained', 'competing_review': None}
    snapshot = {'due_date': '2026-10-14', 'interval_days': 1, 'fsrs_card_json': None,
        'first_correct_at': None, 'reinforcement_pending': 0, 'stability': 2.0, 'guided_review': 0,
        'state': 'learning', 'scheduling_mode': 'normal', 'hard_correct_streak': 0, 'recent_attempts_json': None}
    monkeypatch.setattr(adapter_module, 'training_timing_stage', lambda *_: nullcontext())
    monkeypatch.setattr(adapter_module, 'apply_scheduling_review', lambda *args, **kwargs: owner_result.copy())
    monkeypatch.setattr(adapter_module, 'card_schedule_state', lambda *args: snapshot.copy())
    monkeypatch.setattr(adapter_module, 'reconcile_completed_review', lambda *args, **kwargs: reconciliation.copy())
    command, context = _fixture.command(), _fixture.CONTEXT
    policy = review.ReviewPolicy('America/New_York', 7)
    assert adapter.schedule(command, context, outcome='correct', guided=False, policy=policy).as_dict() == owner_result
    captured = adapter.schedule_snapshot(command.card_id)
    assert isinstance(captured, ScheduleSnapshot) and captured.as_dict() == snapshot
    result = adapter.reconcile_completed(command, _fixture.Capabilities().attempt, context, guided=False, revision=1)
    assert isinstance(result, ReconciledReview) and result.as_dict() == reconciliation
