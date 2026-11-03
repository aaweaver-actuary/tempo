"""Pure classification of accepted position first responses.

This module proposes a grade; it never writes a review or updates a schedule.
The caller must construct ``AcceptedEvidence`` only from the owner's durable,
server-verified first-response receipt and compare its source digest with the
immutable issued proof.  A later graph digest is not a substitute for that proof.
The accepted-ledger writer remains responsible for enforcing one Good per
position per Study day across policies/epochs and for replay idempotency.
"""
from __future__ import annotations

from ..position_learning.contracts import AcceptedEvidence, DueAtIssuance, GradeProposal, Hold


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
