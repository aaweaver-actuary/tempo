"""Structural bindings for the production adapter and an independent schedule owner."""
from typing import Any
from app.services.learning_review_adapter import LearningReviewAdapter
from tempo_learning_review.models import (
    QueueAttempt, ReconciledReview, ReviewCommand, ReviewContext, ReviewPolicy,
    ScheduledReview, ScheduleSnapshot,
)
from tempo_learning_review.ports import ReviewScheduleStore, ReviewStore


class IndependentSchedule:
    def reconcile_completed(self, command: ReviewCommand, attempt: QueueAttempt,
                            context: ReviewContext, *, guided: bool, revision: int | None,
                            competing_review: dict[Any, Any] | None = None) -> ReconciledReview:
        return ReconciledReview.from_dict(dict(persisted=True, review_id=None, queue_entry_id=attempt.id,
                                                requeue_entry_id=None, reconciliation="history_only"))

    def schedule_snapshot(self, card_id: str) -> ScheduleSnapshot | None:
        return None

    def schedule(self, command: ReviewCommand, context: ReviewContext, *, outcome: str,
                 guided: bool, policy: ReviewPolicy) -> ScheduledReview:
        return ScheduledReview(command.card_id, "2026-01-01", 1, "learning", False, None, 1.0, "normal", 0, False, False)

    def record_position_review(self, command: ReviewCommand, context: ReviewContext, *, outcome: str,
                               guided: bool) -> ScheduledReview:
        return ScheduledReview(command.card_id, "2026-01-01", 1, "learning", False, None, 1.0, "normal", 0, False, False)

    def save_snapshot(self, review_id: int, snapshot: ScheduleSnapshot) -> None:
        pass


def production_binding(adapter: LearningReviewAdapter) -> ReviewStore:
    return adapter


def independent_binding() -> ReviewScheduleStore:
    return IndependentSchedule()
