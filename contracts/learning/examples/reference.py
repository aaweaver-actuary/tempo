"""In-memory protocol illustration, NOT a production transaction implementation."""

from copy import deepcopy
from threading import Lock
from tempo_learning_contracts import v1


class ReferenceLearning:
    def __init__(self, issuance: v1.AttemptIssuance):
        self.workspace_id = issuance.workspace_id
        self.attempt = issuance.attempt
        self.learning_revision = issuance.learning_revision
        self.retired = False
        self.effects = {
            "reviews": 0,
            "history": 0,
            "schedule": 0,
            "required_evidence": 0,
            "receipts": 0,
            "queue": 0,
        }
        self.operations = {}
        self.lock = Lock()

    def retire(self):
        with self.lock:
            self.retired = True
            self.learning_revision += 1

    def receive(self, submission: v1.ReviewSubmission, *, pending=False):
        with self.lock:
            key = (submission.workspace_id, submission.submission_id)
            encoded = v1.serialize_contract(submission)
            existing = self.operations.get(key)
            if existing:
                if existing[0] != encoded:
                    return self._reply(
                        submission,
                        {
                            "status": "conflict",
                            "code": "identity_payload_conflict",
                            "message": "Different payload",
                        },
                    )
                if existing[1].settlement.status == "committed" or pending:
                    return deepcopy(existing[1])
            reason = self._stale_reason(submission)
            if reason:
                return self._reply(
                    submission,
                    {
                        "status": "stale_attempt_version",
                        "code": reason,
                        "current_learning_revision": self.learning_revision,
                    },
                )
            if any(
                reply.settlement.status == "committed"
                for _, reply in self.operations.values()
            ):
                return self._reply(
                    submission,
                    {
                        "status": "conflict",
                        "code": "concurrent_review_conflict",
                        "message": "Attempt already settled",
                    },
                )
            if pending:
                reply = self._reply(
                    submission,
                    {
                        "status": "accepted_pending",
                        "operation_id": "operation:" + submission.submission_id,
                    },
                )
            else:
                revision = self.learning_revision + 1
                receipt = v1.ReviewReceipt(
                    receipt_id="receipt:" + submission.submission_id,
                    submission_id=submission.submission_id,
                    attempt_id=submission.attempt_id,
                    card_id=submission.card_id,
                    publication_generation=submission.publication_generation,
                    committed_at="2026-10-14T12:01:01Z",
                    learning_revision=revision,
                    queue_revision=self.attempt.queue_revision + 1,
                    next_due="2026-10-15T12:01:01Z",
                    review_id="review:" + submission.submission_id,
                    required_evidence_ids=["evidence:" + submission.submission_id],
                )
                reply = self._reply(
                    submission, {"status": "committed", "receipt": receipt.model_dump()}
                )
                # One lock-protected assignment illustrates the indivisible owner.
                self.effects = {
                    effect: count + 1 for effect, count in self.effects.items()
                }
                self.learning_revision = revision
            self.operations[key] = (encoded, reply)
            return deepcopy(reply)

    def _reply(self, submission, settlement):
        return v1.ReviewSettlement(
            contract_version=1,
            workspace_id=submission.workspace_id,
            submission_id=submission.submission_id,
            settlement=settlement,
        )

    def _stale_reason(self, submission):
        exercise = self.attempt.exercise
        if submission.workspace_id != self.workspace_id:
            return "superseded_attempt"
        if self.retired:
            return "retired_publication"
        if (
            submission.attempt_id,
            submission.attempt_revision,
            submission.card_id,
            submission.exercise_revision,
        ) != (
            self.attempt.attempt_id,
            self.attempt.attempt_revision,
            exercise.card_id,
            exercise.exercise_revision,
        ):
            return "superseded_attempt"
        if (submission.publication_id, submission.publication_generation) != (
            exercise.publication_id,
            exercise.publication_generation,
        ):
            return "publication_generation_changed"
        if (submission.queue_entry_id, submission.queue_revision) != (
            self.attempt.queue_entry_id,
            self.attempt.queue_revision,
        ):
            return "queue_revision_changed"
        return None
