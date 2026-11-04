"""Pure position first-response grading; the accepted writer owns provenance.

Ported from legacy Study policy. This module does not read staging, write
receipts, own transactions or authorize a scheduler transition. The caller
must supply locked original evidence and all same-position/day Good receipts,
including earlier epochs.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Literal
from tempo_evidence_math.position_learning import (
    AcceptedEvidence, DueAtIssuance, GradeProposal, Hold, PositionDefinition,
)
from tempo_scheduling import PositionSchedule, schedule_position_review


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
        if self.last_transition_at is not None and self.last_transition_at.utcoffset() is None:
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
        if (original.attempt_id, original.index) == (response.attempt_id, response.index):
            return Hold("first_response_event_id_changed")

    if not evidence.saved:
        return Hold("evidence_not_durable")
    if not evidence.source_digest:
        return Hold("source_proof_unavailable")
    if (proposal.event_id != response.event_id
            or proposal.position != response.definition.key
            or proposal.policy_version != response.definition.policy_version
            or proposal.observed_at != response.observed_at
            or proposal.accepted_at != evidence.accepted_at):
        return Hold("proposal_evidence_mismatch")
    if proposal.grade not in {"good", "again", "observe", "pending"}:
        return Hold("invalid_grade")
    if proposal.issued_position_revision < 1:
        return Hold("invalid_issued_position_revision")
    if (proposal.accepted_at.utcoffset() is None
            or proposal.observed_at.utcoffset() is None):
        return Hold("grade_timestamp_aware_required")
    if (proposal.owner_epoch != current.owner_epoch
            or evidence.owner_epoch != current.owner_epoch):
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
        if response.correct or evidence.disposition not in {"wrong", "illegal", "manual_failure"}:
            return Hold("again_requires_verified_first_wrong")
        if proposal.study_day != current.study_day:
            return Hold("negative_requires_accepted_study_day")
    elif grade == "good":
        if not response.correct or not evidence.terminal_complete or not evidence.aggregate_review_saved:
            return Hold("good_requires_completed_saved_recall")
        if proposal.study_day != current.study_day:
            grade, reason = "observe", "accepted_after_issued_study_day"
        elif proposal.issued_position_revision != current.revision:
            grade, reason = "observe", "stale_due_issuance"
        elif any(receipt.final.position == proposal.position
                 and receipt.final.study_day == proposal.study_day
                 and receipt.final.grade == "good" for receipt in receipts):
            grade, reason = "observe", "already_good_today"

    if (grade in {"good", "again"} and current.last_transition_at is not None
            and proposal.accepted_at < current.last_transition_at):
        return Hold("acceptance_precedes_current_transition")

    final = replace(proposal, grade=grade, reason=reason)
    resulting_revision = current.revision + int(grade in {"good", "again"})
    return GradeAcceptance(AcceptedGradeReceipt(proposal, evidence, final, resulting_revision))


def propose_grade(evidence: AcceptedEvidence, due: DueAtIssuance) -> GradeProposal | Hold:
    """Classify one owner's accepted first response against its issuance witness.

    A terminal saved aggregate is required for a positive Good. An independently
    durable wrong first response can propose Again before completion, including
    when nondue or accepted after an earlier Good. Ledger uniqueness and replay
    checks are deliberately left to the transactional grade owner.
    """
    response = evidence.first_response

    if not evidence.saved:
        return Hold("evidence_not_durable")
    if not evidence.source_digest:
        return Hold("source_proof_unavailable")
    if evidence.owner_epoch != due.owner_epoch:
        return Hold("owner_epoch_changed")
    if response.definition != due.definition:
        return Hold("response_definition_changed")
    if response.attempt_id == "" or not response.event_id:
        return Hold("first_response_identity_missing")

    if evidence.disposition in {"alternate", "unverified"}:
        return Hold("first_response_not_authoritatively_classified")

    correct = response.correct
    if correct != (evidence.disposition == "expected"):
        return Hold("first_response_disposition_conflict")
    if evidence.disposition not in {"expected", "wrong", "illegal", "manual_failure"}:
        return Hold("first_response_not_authoritatively_classified")
    if evidence.disposition == "manual_failure" and not response.manual_failure:
        return Hold("first_response_disposition_conflict")
    if evidence.disposition in {"wrong", "illegal"} and (correct or response.manual_failure):
        return Hold("first_response_disposition_conflict")

    # A response after guidance is coached evidence, not an independent failure
    # or unaided recall.  Durable unassisted negatives remain eligible regardless
    # of completion, due status, or a prior Good; retry/replay de-dupe is external.
    if response.assistance_before:
        grade, reason = "observe", "assisted_first_response_no_new_grade"
    elif not correct:
        grade, reason = "again", "actual_first_wrong_or_manual_failure"
    elif not due.due:
        grade, reason = "observe", "nondue_correct_no_schedule"
    elif not evidence.terminal_complete or not evidence.aggregate_review_saved:
        grade, reason = "pending", "correct_waits_for_completed_saved_review"
    elif evidence.accepted_study_day != due.study_day:
        grade, reason = "observe", "accepted_after_issued_study_day"
    else:
        grade, reason = "good", "due_clean_first_response"

    # The Study owner supplies this authoritative day at acceptance and retains
    # it on replay. Never infer it from timestamp timezone offsets.
    study_day = (
        evidence.accepted_study_day
        if grade == "again"
        else due.study_day
    )
    return GradeProposal(
        position=response.definition.key,
        policy_version=response.definition.policy_version,
        event_id=response.event_id,
        study_day=study_day,
        owner_epoch=due.owner_epoch,
        issued_position_revision=due.position_revision,
        grade=grade,
        reason=reason,
        observed_at=response.observed_at,
        accepted_at=evidence.accepted_at,
    )


@dataclass(frozen=True)
class PositionGradeDecision:
    acceptance: GradeAcceptance
    schedule: PositionSchedule | None


def decide_position_grade(evidence: AcceptedEvidence, due: DueAtIssuance,
                          current: CurrentPositionState,
                          receipts: tuple[AcceptedGradeReceipt, ...],
                          fsrs_json: str) -> PositionGradeDecision | Hold:
    """Compute a proposal only; the accepted writer must atomically apply it."""
    proposal = propose_grade(evidence, due)
    if isinstance(proposal, Hold):
        return proposal
    acceptance = accept_grade(proposal, current, receipts, evidence=evidence)
    if isinstance(acceptance, Hold):
        return acceptance
    schedule = None
    if acceptance.schedule_grade is not None:
        schedule = schedule_position_review(
            acceptance.schedule_grade, fsrs_card_json=fsrs_json,
            accepted_at=evidence.accepted_at, review_day=evidence.accepted_study_day,
        )
    return PositionGradeDecision(acceptance, schedule)
