"""Pure acceptance reducer for position Study grades and immutable receipts.

The Study writer supplies authoritative current state, acceptance day and all
relevant accepted receipts under its transaction: event/attempt receipts and any
Good for this position/day, including earlier policy versions and owner epochs.
This function neither establishes that provenance nor proves concurrency safety.
It performs no clock, timezone, scheduler, database or service operations.

C1/server authority must bind each event to its original attempt/decision and
verify that it is a genuinely independent first response. Changing an event ID
cannot turn a retry of that response into a fresh failure episode. Observation
receipts are retained too. Pending correct responses stay in the raw evidence
journal and do not finalize a grade receipt until saved completion.
After accepted Again, terminal completion attaches settlement separately: replay
the original negative receipt rather than resubmitting changed aggregate evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Literal

from tempo_evidence_math.position_learning import (
    AcceptedEvidence,
    GradeProposal,
    Hold,
    PositionDefinition,
)


@dataclass(frozen=True)
class CurrentPositionState:
    """Facts fenced by Study at acceptance, not client or issuance assertions."""

    definition: PositionDefinition
    owner_epoch: str
    revision: int
    study_day: date
    last_transition_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.revision < 1 or not self.owner_epoch:
            raise ValueError("invalid_current_position_state")
        if (
            self.last_transition_at is not None
            and self.last_transition_at.utcoffset() is None
        ):
            raise ValueError("last_transition_at_aware_required")


@dataclass(frozen=True)
class AcceptedGradeReceipt:
    """Immutable original evidence, requested classification and final outcome."""

    proposal: GradeProposal
    evidence: AcceptedEvidence
    final: GradeProposal
    resulting_revision: int


@dataclass(frozen=True)
class GradeAcceptance:
    receipt: AcceptedGradeReceipt
    replayed: bool = False

    @property
    def schedule_grade(self) -> Literal["good", "again"] | None:
        """Only a fresh transition is eligible for the writer's atomic effect."""
        grade = self.receipt.final.grade
        return grade if not self.replayed and grade in {"good", "again"} else None


def accept_grade(
    proposal: GradeProposal,
    current: CurrentPositionState,
    receipts: tuple[AcceptedGradeReceipt, ...],
    *,
    evidence: AcceptedEvidence,
) -> GradeAcceptance | Hold:
    """Accept in caller order; never reorder/backdate by observation timestamps.

    Exact replay returns the original receipt even if the current day, policy,
    epoch or revision has since changed. New scheduling effects use the owner's
    acceptance timestamp and current day. An acceptance timestamp preceding an
    already applied transition holds rather than retroactively rewriting FSRS.
    A stale issued revision suppresses Good only; distinct new wrong evidence
    still proposes Again against the current state at actual acceptance.
    """
    for receipt in receipts:
        if receipt.proposal.event_id == proposal.event_id:
            if receipt.proposal != proposal or receipt.evidence != evidence:
                return Hold("event_id_reused_with_changed_payload")
            return GradeAcceptance(receipt, replayed=True)

    response = evidence.first_response
    for receipt in receipts:
        original = receipt.evidence.first_response
        if (original.attempt_id, original.index) == (
            response.attempt_id,
            response.index,
        ):
            return Hold("first_response_event_id_changed")

    if not evidence.saved:
        return Hold("evidence_not_durable")
    if not evidence.source_digest:
        return Hold("source_proof_unavailable")
    if (
        proposal.event_id != response.event_id
        or proposal.position != response.definition.key
        or proposal.policy_version != response.definition.policy_version
        or proposal.observed_at != response.observed_at
        or proposal.accepted_at != evidence.accepted_at
    ):
        return Hold("proposal_evidence_mismatch")
    if proposal.grade not in {"good", "again", "observe", "pending"}:
        return Hold("invalid_grade")
    if proposal.issued_position_revision < 1:
        return Hold("invalid_issued_position_revision")
    if (
        proposal.accepted_at.utcoffset() is None
        or proposal.observed_at.utcoffset() is None
    ):
        return Hold("grade_timestamp_aware_required")
    if (
        proposal.owner_epoch != current.owner_epoch
        or evidence.owner_epoch != current.owner_epoch
    ):
        return Hold("owner_epoch_changed")
    if response.definition != current.definition:
        return Hold("response_definition_changed")
    if evidence.accepted_study_day != current.study_day:
        return Hold("accepted_study_day_mismatch")
    if evidence.disposition in {"alternate", "unverified"}:
        return Hold("first_response_not_authoritatively_classified")
    if response.correct != (evidence.disposition == "expected"):
        return Hold("first_response_disposition_conflict")
    if evidence.disposition == "manual_failure" and not response.manual_failure:
        return Hold("first_response_disposition_conflict")
    if evidence.disposition in {"wrong", "illegal"} and response.manual_failure:
        return Hold("first_response_disposition_conflict")

    # The durable raw evidence journal preserves partial observations. Do not
    # freeze an incomplete classification into a grade receipt that would block
    # its later completed saved recall under this same first-response identity.
    if proposal.grade == "pending":
        return Hold("awaiting_saved_completion")

    grade, reason = proposal.grade, proposal.reason
    if response.assistance_before:
        grade, reason = "observe", "assisted_first_response_no_new_grade"
    elif grade == "again":
        if response.correct or evidence.disposition not in {
            "wrong",
            "illegal",
            "manual_failure",
        }:
            return Hold("again_requires_verified_first_wrong")
        if proposal.study_day != current.study_day:
            return Hold("negative_requires_accepted_study_day")
    elif grade == "good":
        if (
            not response.correct
            or not evidence.terminal_complete
            or not evidence.aggregate_review_saved
        ):
            return Hold("good_requires_completed_saved_recall")
        if proposal.study_day != current.study_day:
            grade, reason = "observe", "accepted_after_issued_study_day"
        elif proposal.issued_position_revision != current.revision:
            grade, reason = "observe", "stale_due_issuance"
        elif any(
            receipt.final.position == proposal.position
            and receipt.final.study_day == proposal.study_day
            and receipt.final.grade == "good"
            for receipt in receipts
        ):
            grade, reason = "observe", "already_good_today"

    if (
        grade in {"good", "again"}
        and current.last_transition_at is not None
        and proposal.accepted_at < current.last_transition_at
    ):
        return Hold("acceptance_precedes_current_transition")

    final = replace(proposal, grade=grade, reason=reason)
    resulting_revision = current.revision + int(grade in {"good", "again"})
    return GradeAcceptance(
        AcceptedGradeReceipt(proposal, evidence, final, resulting_revision)
    )
