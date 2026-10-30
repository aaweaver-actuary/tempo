"""Read-only receiving-owner facts, separate from any promised service time."""
from typing import TypedDict

from app.contracts.product_v02.learning import EncounterProgress,IssuedAttemptEvidence


class QueuedFeedbackAttempt(TypedDict):
    queue_entry_id: int
    queue_date: str
    status: str
    attempt_state: str
    card_id: str
    card_revision: int
    issuance: IssuedAttemptEvidence | None


class FeedbackObservation(TypedDict):
    progress: EncounterProgress | None
    target_compatible: bool
    queued_attempt: QueuedFeedbackAttempt | None
    issued_since_determination: int | None
