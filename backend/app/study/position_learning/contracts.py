"""Study transaction ports and identity-preserving shared value exports."""
from __future__ import annotations

from typing import Protocol, TypeVar
from tempo_evidence_math.position_learning import (
    AcceptedEvidence, DecisionOccurrence, DueAtIssuance, FirstResponse, Grade,
    GradeProposal, Hold, PositionDefinition, PositionKey, RouteOption,
    SourceCertificate,
)

# Transaction owners supply concrete proposal and receipt values. These ports do
# not open or commit a transaction and a pure proposal is not an issuance.
IssuedPlanT = TypeVar("IssuedPlanT", contravariant=True)
IssuedReceiptT = TypeVar("IssuedReceiptT", covariant=True)
GradeReceiptT = TypeVar("GradeReceiptT", covariant=True)
OwnerReceiptT = TypeVar("OwnerReceiptT", covariant=True)
PositionStateT = TypeVar("PositionStateT", covariant=True)


class PositionScheduleReadPort(Protocol[PositionStateT]):
    def read_due_page(self, *, repertoire_id: str, owner_epoch: str,
            after: tuple | None, limit: int) -> tuple[DueAtIssuance, ...] | Hold: ...

    def read_current(self, position: PositionKey) -> PositionStateT | Hold: ...


class IssuedPositionAttemptPort(Protocol[IssuedPlanT, IssuedReceiptT]):
    def issue_in_borrowed_transaction(self, original_queue_id: int,
            proposal: IssuedPlanT, *, source_epoch: str) -> IssuedReceiptT | Hold: ...

    def replay_exact_attempt(self, attempt_id: str) -> IssuedReceiptT | Hold: ...


class GradeEffectWritePort(Protocol[GradeReceiptT]):
    def apply_in_borrowed_transaction(self, accepted: tuple[GradeProposal, ...],
            original_attempt_id: str, *, owner_epoch: str) -> tuple[GradeReceiptT, ...] | Hold: ...


class PositionOwnerEpochReadPort(Protocol[OwnerReceiptT]):
    def fence_in_borrowed_transaction(self, repertoire_id: str, epoch: str,
            source_digest: str) -> OwnerReceiptT | Hold: ...
