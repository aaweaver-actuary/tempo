"""Review owner sequencing for position-owned completions."""

import importlib.util
from pathlib import Path

import pytest
import tempo_learning_review as review
from tempo_learning_review.models import ScheduledReview

_source = (
    Path(__file__).resolve().parents[2]
    / "services/learning/application/tests/test_application.py"
)
_spec = importlib.util.spec_from_file_location("review_application_fixture", _source)
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)


class PositionStore(_fixture.Capabilities):
    def begin_position_review(self, command):
        self.event("owner_fence")
        return True

    def record_position_review(self, command, context, *, outcome, guided):
        self.event("human_review")
        return ScheduledReview.from_dict({**self.scheduled, "requeue_today": False, "requeue_after_cards": None})

    def lock_position_attempt(self, command):
        self.event("attempt_lock")

    def apply_position_effects(self, command, context, result):
        self.event("position_effects")

    def save_position_receipt(self, command, context, result):
        self.save_receipt(command, context, result)

    def finish_position_receipt(self, command, context, result):
        self.event("finish_receipt")


def test_position_completion_orders_evidence_human_receipt_and_effects_without_card_schedule():
    store = PositionStore()
    command = _fixture.command(completion=_fixture.completion())
    result = review.submit_review(store, command, lambda: _fixture.CONTEXT).as_dict()
    names = [call[0] for call in store.calls]
    assert (
        names.index("owner_fence")
        < names.index("cards_lock")
        < names.index("queue_lock")
        < names.index("attempt_lock")
    )
    assert (
        names.index("evidence") < names.index("human_review") < names.index("receipt")
    )
    assert (
        names.index("receipt")
        < names.index("complete_evidence")
        < names.index("position_effects")
        < names.index("finish_receipt")
        < names.index("bind")
    )
    assert not {"schedule", "snapshot", "clean", "requeue"} & set(names)
    assert result["persisted"] and result["requeue_entry_id"] is None
    review.submit_review(store, command, lambda: _fixture.CONTEXT)
    assert sum(call[0] == "position_effects" for call in store.calls) == 1


def test_position_effect_failure_prevents_binding():
    store = PositionStore()
    store.failure = "position_effects"
    with pytest.raises(RuntimeError, match="position_effects"):
        review.submit_review(
            store,
            _fixture.command(completion=_fixture.completion()),
            lambda: _fixture.CONTEXT,
        )
    assert "bind" not in [call[0] for call in store.calls]


def test_position_human_review_does_not_save_card_schedule(monkeypatch):
    from types import SimpleNamespace
    from app.services.learning_review_adapter import LearningReviewAdapter
    from tempo_storage import scheduling_review_store

    card = SimpleNamespace(
        due_date="2026-11-10",
        interval_days=8,
        state="learning",
        stability=2.5,
        scheduling_mode="normal",
        hard_correct_streak=0,
    )
    saved = []

    class ReviewStore:
        def __init__(self, database):
            pass

        def load_card_state(self, card_id, *, require_active):
            return card

        def append_review(self, record):
            saved.append(record)

        def save_card_state(self, *args):
            pytest.fail("position review changed physical schedule")

    monkeypatch.setattr(
        scheduling_review_store, "OwnedConnectionSchedulingReviewStore", ReviewStore
    )
    adapter = LearningReviewAdapter(object(), object())
    result = adapter.record_position_review(
        _fixture.command(), _fixture.CONTEXT, outcome="correct", guided=False
    ).as_dict()
    assert (
        result["next_due"] == card.due_date
        and result["interval_days"] == card.interval_days
    )
    assert not result["requeue_today"]
    assert len(saved) == 1 and saved[0].source_kind == "position_study"
    from app.services.scheduling_review_contracts import ReviewRecord

    assert type(saved[0]) is ReviewRecord
    assert saved[0].source_ref == "attempt:attempt-a"
    assert saved[0].previous_interval == saved[0].next_interval == card.interval_days
