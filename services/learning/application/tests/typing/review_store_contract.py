"""Structural bindings for the production adapter and an independent schedule owner."""
from app.services.learning_review_adapter import LearningReviewAdapter
from tempo_learning_review.models import (
    AttemptReceipt, CardFacts, CompetingReview, ContinuationFacts, PreparedNext, QueueAttempt, ReconciledReview, ReviewCommand, ReviewContext, ReviewPolicy, ReviewResult,
    ScheduledReview, ScheduleSnapshot, EvidenceCompletion,
)
from tempo_learning_review.ports import ReviewCompletionStore, ReviewScheduleStore, ReviewSubmissionStore


class IndependentSchedule:
    def reconcile_completed(self, command: ReviewCommand, attempt: QueueAttempt,
                            context: ReviewContext, *, guided: bool, revision: int | None,
                            competing_review: CompetingReview | None = None) -> ReconciledReview:
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


def production_binding(adapter: LearningReviewAdapter) -> ReviewSubmissionStore:
    return adapter


def independent_binding() -> ReviewScheduleStore:
    return IndependentSchedule()


class IndependentCompletion:
    def apply_position_effects(self, command: ReviewCommand, context: ReviewContext, result: ReviewResult, completion: EvidenceCompletion) -> None: pass
    def finish_position_receipt(self, command: ReviewCommand, context: ReviewContext, result: ReviewResult) -> None: pass
    def complete_evidence(self, command: ReviewCommand, result: ReviewResult) -> None: pass
    def inherit_evidence_scope(self, command: ReviewCommand, result: ReviewResult) -> None: pass
    def continuation_facts(self, candidate: PreparedNext) -> ContinuationFacts | None: return None
    def issue_head(self, candidate: PreparedNext) -> bool: return False
    def save_continuation(self, attempt_id: str, result: ReviewResult) -> None: pass


def independent_completion() -> ReviewCompletionStore:
    return IndependentCompletion()


class IndependentSubmission(IndependentSchedule, IndependentCompletion):
    def is_deleted(self, card_id: str) -> bool: return False
    def receipt(self, attempt_id: str) -> AttemptReceipt | None: return None
    def card(self, card_id: str) -> CardFacts | None: return None
    def find_attempt(self, command: ReviewCommand, context: ReviewContext) -> QueueAttempt | None: return None
    def recover_attempt(self, command: ReviewCommand) -> QueueAttempt: raise ValueError("no attempt")
    def latest_review_id(self, card_id: str, *, valid_only: bool) -> int: return 0
    def current_revision(self, card_id: str) -> int | None: return None
    def policy(self) -> ReviewPolicy: return ReviewPolicy("local", 1)
    def retain_introduction(self, card_id: str, queue_day: str) -> None: pass
    def has_later_review(self, card_id: str, context: ReviewContext) -> bool: return False
    def bind_receipt(self, command: ReviewCommand, request_json: str, *, restore_original: bool = False) -> None: pass
    def complete_attempt(self, command: ReviewCommand, context: ReviewContext, *, guided: bool) -> None: pass
    def mark_clean_tactic(self, card_id: str, context: ReviewContext) -> None: pass
    def requeue(self, attempt: QueueAttempt, card_id: str, after: int | None, state: str) -> int | None: return None
    def save_receipt(self, command: ReviewCommand, context: ReviewContext, result: ReviewResult) -> None: pass
    def save_position_receipt(self, command: ReviewCommand, context: ReviewContext, result: ReviewResult) -> None: pass
    def retain_completion(self, command: ReviewCommand, attempt: QueueAttempt, result_json: str, *, origin_only: bool = False) -> None: pass
    def refresh_progression(self, context: ReviewContext, card: CardFacts, result: ReviewResult) -> None: pass
    def refresh_priority(self, repertoire_id: str, *, transactional_only: bool = False) -> None: pass
    def refresh_maturity(self, context: ReviewContext) -> None: pass
    def begin_position_review(self, command: ReviewCommand) -> bool: return False
    def lock_position_attempt(self, command: ReviewCommand) -> None: pass
    def lock_review_cards(self, command: ReviewCommand) -> bool: return False
    def lock_queue_date(self) -> None: pass
    def completed_evidence_exists(self, attempt_id: str) -> bool: return False
    def evidence_revision_allowed(self, command: ReviewCommand, completion: EvidenceCompletion) -> bool: return False
    def persist_evidence(self, command: ReviewCommand, completion: EvidenceCompletion) -> None: pass


def independent_submission() -> ReviewSubmissionStore:
    return IndependentSubmission()
