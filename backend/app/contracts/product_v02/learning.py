"""Study-owned semantic operations. This module implements no scheduling or storage."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Literal, Protocol

from pydantic import ConfigDict, Field, field_validator, model_validator
from tempo_game_evidence_contracts.v2 import EvidenceWithdrawal
from tempo_game_evidence_contracts.v3 import EvidenceDelivery

from . import Digest, Identifier, Revision, UciMove, UtcTimestamp, Value
from .games import Finding, FirstDepartureSelection
from .repertoire import DecisionPresentation, IntroductionGameScope


class ImmutableEvidenceWithdrawal(EvidenceWithdrawal):
    """Existing v2/v3 wire fields with assignment frozen only in this new seam."""
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class GameEffectKey(Value):
    workspace_id: Identifier
    provider: Literal["lichess", "chesscom"]
    game_id: Identifier
    decision_id: Identifier
    occurrence_ply: int = Field(ge=1, le=1000)

    def logical_id(self) -> str:
        """Collision-free stable identity; excludes delivery/comparison/source revisions."""
        return json.dumps([self.workspace_id, self.provider, self.game_id, self.decision_id, self.occurrence_ply], separators=(",", ":"), ensure_ascii=False)


class GameLapse(Value):
    key: GameEffectKey
    finding: Finding
    detected_at: UtcTimestamp
    occurred_at: UtcTimestamp | None
    payload_digest: Digest

    @model_validator(mode="after")
    def confirmed_same_occurrence(self):
        if self.finding.kind != "confirmed_miss" or (self.key.provider, self.key.game_id, self.key.decision_id, self.key.occurrence_ply) != (self.finding.provider, self.finding.game_id, self.finding.decision_id, self.finding.ply):
            raise ValueError("lapse must identify the same confirmed decision occurrence")
        return self


class GameEffectControl(Value):
    key: GameEffectKey
    source_revision: Revision
    action: Literal["hold", "withdraw"]
    reason: Literal["source_corrected", "source_excluded", "source_deleted", "reanalysis"] | None = None
    withdrawal: ImmutableEvidenceWithdrawal | None = None

    @field_validator("withdrawal", mode="before")
    @classmethod
    def copy_existing_withdrawal(cls, value):
        if value is None:
            return None
        payload = value.model_dump(mode="json") if isinstance(value, EvidenceWithdrawal) else value
        return ImmutableEvidenceWithdrawal.model_validate_json(json.dumps(payload))

    @model_validator(mode="after")
    def reason_without_owner_identity(self):
        if self.reason is None and self.withdrawal is None:
            raise ValueError("control requires a reason; owner obligation identity is optional")
        if self.reason is not None and self.withdrawal is not None and self.reason != self.withdrawal.reason:
            raise ValueError("control and existing withdrawal reasons must agree")
        return self


class LearningReceipt(Value):
    key: GameEffectKey
    source_revision: Revision
    status: Literal["pending", "held", "applied", "settled"]
    outcome: Literal["accepted", "duplicate", "conflict", "stale_revision", "withdrawn", "historical_preserved"]
    owner_receipt_id: Identifier
    admitted_at: UtcTimestamp | None
    available_by: UtcTimestamp | None
    applied_at: UtcTimestamp | None
    reason: Identifier | None


class LearningFeedbackPort(Protocol):
    """Legacy command compatibility; Study owns persistence and scheduling.

    accept_lapse: one effect per logical key. New first-departure delivery uses
    qualified intake below; the old chronology-bearing command stays readable.
    Changed payload conflicts/quarantines; it is never a second lapse.
    control_effect: durably fence even a not-yet-delivered original; repeated calls
    are idempotent. No delayed/stale delivery may release that fence. Preserve an
    already-applied historical effect; neither hold nor withdrawal reverses it.
    Producers must control an existing effect even after reclassification to a
    non-miss. A new receipt is not permission to release quarantined evidence.
    """
    def accept_lapse(self, lapse: GameLapse) -> LearningReceipt: ...
    def control_effect(self, command: GameEffectControl) -> LearningReceipt: ...
    def effect_status(self, key: GameEffectKey) -> LearningReceipt | None: ...


class FirstDepartureWitness(Value):
    """Prepared qualification, separate from original immutable command bytes.

    This is not authority by itself. The owning Study adapter must revalidate
    the actual provider configuration/source and every relevant publication
    against trusted owner captures outside/inside its bounded write boundary.
    """
    selection: FirstDepartureSelection
    configuration_revision: Revision
    determined_at: UtcTimestamp
    stream: Literal["forward", "reconciliation", "manual"]

    @model_validator(mode="after")
    def only_exact_learner_departure(self):
        if self.selection.status != "own_actionable":
            raise ValueError("only an exact learner first departure can qualify Study intake")
        return self

    def matches(self, command: GameEncounter | GameLapse) -> bool:
        """Bind original occurrence facts without replacing its payload/digest."""
        selected, key = self.selection, command.key
        if isinstance(command, GameEncounter):
            source, decision, expected, played = command.source, command.decision, (command.expected_uci,), command.played_uci
            if command.observation != "missed":
                return False
        else:
            finding = command.finding
            source = IntroductionGameScope(provider=finding.provider, game_id=finding.game_id,
                source_revision=finding.source_revision, source_digest=finding.source_digest)
            decision, expected, played = finding, finding.expected_uci, finding.played_uci
        return bool(selected.decision is not None and selected.source == source
            and selected.workspace_id == key.workspace_id and selected.ply == key.occurrence_ply
            and selected.decision.decision_id == key.decision_id == decision.decision_id
            and selected.position_key == decision.position_key
            and selected.expected_uci == expected and selected.played_uci == played)


class EncounterTarget(Value):
    """Actual current physical presentation; original decision identity is separate."""
    card_id: Identifier
    card_revision: Revision


class IssuedAttemptEvidence(Value):
    """Queue-owner first issuance, never a prefetch/reload or invented old stamp."""
    queue_entry_id: int = Field(ge=1, le=2**53 - 1)
    card_id: Identifier
    card_revision: Revision
    issuance_sequence: int = Field(ge=1, le=2**53 - 1)
    issued_at: UtcTimestamp


class RemediationFence(Value):
    target: EncounterTarget
    after_issuance_sequence: int = Field(ge=0, le=2**53 - 1)
    established_at: UtcTimestamp

    def precedes(self, attempt: IssuedAttemptEvidence) -> bool:
        return bool((attempt.card_id, attempt.card_revision) == (self.target.card_id, self.target.card_revision)
            and attempt.issuance_sequence > self.after_issuance_sequence
            and datetime.fromisoformat(attempt.issued_at.replace("Z", "+00:00")) >=
                datetime.fromisoformat(self.established_at.replace("Z", "+00:00")))


class EncounterSettlement(Value):
    """Study's durable genuine clean completion receipt and its actual issuance."""
    review_id: int = Field(ge=1, le=2**53 - 1)
    attempt: IssuedAttemptEvidence


class EncounterProgress(Value):
    policy_version: Literal["first_departure_v1"] = "first_departure_v1"
    determined_at: UtcTimestamp
    stream: Literal["forward", "reconciliation", "manual"]
    target: EncounterTarget
    scheduling_review_id: int | None = Field(ge=1, le=2**53 - 1)
    schedule_applied_at: UtcTimestamp | None
    introduction_applied_at: UtcTimestamp | None
    remediation_fence: RemediationFence | None
    first_issued: IssuedAttemptEvidence | None
    first_issued_ordinal: int | None = Field(ge=1, le=2**53 - 1)
    settlement: EncounterSettlement | None

    @model_validator(mode="after")
    def actual_application_and_issuance_evidence(self):
        if (self.scheduling_review_id is None) != (self.schedule_applied_at is None):
            raise ValueError("Again application needs the actual scheduler review and application time")
        if self.schedule_applied_at is not None and self.introduction_applied_at is not None:
            raise ValueError("one accepted disposition cannot claim both Again and first introduction")
        application = self.schedule_applied_at or self.introduction_applied_at
        if (self.remediation_fence is None) != (application is None):
            raise ValueError("application/admission and its issuance fence must be recorded together")
        if self.remediation_fence is not None:
            if (self.remediation_fence.target != self.target
                    or datetime.fromisoformat(self.remediation_fence.established_at.replace("Z", "+00:00")) <
                       datetime.fromisoformat(application.replace("Z", "+00:00"))):
                raise ValueError("remediation fence must bind the actual target and application boundary")
        if (self.first_issued is None) != (self.first_issued_ordinal is None):
            raise ValueError("actual first issuance and its measured ordinal must be supplied together")
        if self.first_issued is not None and (self.remediation_fence is None or not self.remediation_fence.precedes(self.first_issued)):
            raise ValueError("first remedial issuance must be a genuine post-application target generation")
        if self.first_issued is not None and (
                datetime.fromisoformat(self.first_issued.issued_at.replace("Z", "+00:00")) <
                datetime.fromisoformat(self.determined_at.replace("Z", "+00:00"))):
            raise ValueError("target issuance cannot precede original actionable determination")
        if self.settlement is not None and (self.first_issued is None or self.remediation_fence is None
                or not self.remediation_fence.precedes(self.settlement.attempt)
                or self.settlement.attempt.issuance_sequence < self.first_issued.issuance_sequence):
            raise ValueError("settlement requires a genuine compatible post-application issued attempt")
        if self.settlement is not None:
            first, completed = self.first_issued, self.settlement.attempt
            same_generation = ((first.queue_entry_id, first.card_id, first.card_revision) ==
                               (completed.queue_entry_id, completed.card_id, completed.card_revision))
            same_sequence = first.issuance_sequence == completed.issuance_sequence
            same_time = (datetime.fromisoformat(first.issued_at.replace("Z", "+00:00")) ==
                         datetime.fromisoformat(completed.issued_at.replace("Z", "+00:00")))
            if (same_sequence and not same_generation) or (same_generation and not (same_sequence and same_time)):
                raise ValueError("one once-stamped issuance must retain its actual generation, sequence and time")
        return self


class GameEncounter(Value):
    """Next-release source facts, not a producer decision to introduce or lapse.

    Stored 0.2.1 commands retain their original facts and digest. Matched play
    is frequency evidence; only a qualified first learner departure can now
    create urgent introduction/Again. Study chooses disposition at acceptance.
    """
    interface_version: Literal["0.2.1"] = "0.2.1"
    key: GameEffectKey
    source: IntroductionGameScope
    decision: DecisionPresentation
    expected_uci: UciMove
    played_uci: UciMove
    observation: Literal["matched", "missed"]
    detected_at: UtcTimestamp
    occurred_at: UtcTimestamp | None
    payload_digest: Digest

    @model_validator(mode="after")
    def same_source_and_decision_occurrence(self):
        if (self.key.provider, self.key.game_id, self.key.decision_id) != (
                self.source.provider, self.source.game_id, self.decision.decision_id):
            raise ValueError("encounter key must identify the same provider/game/decision")
        if self.expected_uci != self.decision.selected_response_uci:
            raise ValueError("expected move must be the supplied approved decision response")
        if (self.observation == "matched") != (self.played_uci == self.expected_uci):
            raise ValueError("observation must preserve actual expected/played facts")
        if self.decision.introduction_game is not None and self.decision.introduction_game != self.source:
            raise ValueError("supplied introduction chronology must identify this exact game source")
        return self


class EncounterReceipt(LearningReceipt):
    """Owner disposition, separately recording relevance and actual admission.

    Existing LearningReceipt fields/statuses are reused, never relabelled as
    queue availability. Encounter first introductions bypass ordinary allowance
    and Study-only controls; explicit full pause and safety guards still apply.
    """
    interface_version: Literal["0.2.1", "0.2.2"] = "0.2.1"
    detected_at: UtcTimestamp
    source_digest: Digest
    observation: Literal["matched", "missed"]
    observation_recorded: bool
    disposition: Literal["review_priority", "first_introduction", "existing_lapse", "frequency_only", "introduced_again"] | None
    admission_status: Literal["pending", "available", "settled", "not_requested"]
    deferred_reason: Literal["study_only", "disabled", "paused", "zero_limit", "introduction_allowance", "active_attempt", "authority_unavailable", "unknown_chronology"] | None
    progress: EncounterProgress | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="before")
    @classmethod
    def optional_progress_is_omitted_not_null(cls, value):
        if isinstance(value, dict) and "progress" in value and value["progress"] is None:
            raise ValueError("legacy receipt omits progress; new policy requires actual progress")
        return value

    @model_validator(mode="after")
    def truthful_disposition_and_deferral(self):
        if self.observation == "matched" and self.disposition == "existing_lapse":
            raise ValueError("correct play cannot produce a lapse")
        if self.disposition == "frequency_only" and self.admission_status != "not_requested":
            raise ValueError("frequency-only evidence cannot claim Study admission")
        if self.disposition == "frequency_only" and self.observation != "matched":
            raise ValueError("historical missed-response importance cannot be reduced to frequency-only")
        if self.admission_status == "available" and (self.disposition is None or self.status == "held"):
            raise ValueError("unresolved or held evidence cannot claim availability")
        if self.deferred_reason is not None and self.admission_status != "pending":
            raise ValueError("deferred work must remain visibly pending")
        if self.admission_status == "pending" and self.deferred_reason is None and self.reason is None:
            raise ValueError("pending encounter admission requires an owner reason")
        if self.disposition == "first_introduction" and self.deferred_reason in {
                "study_only", "zero_limit", "introduction_allowance"}:
            raise ValueError("real-game first introduction is a targeted exception to ordinary cap and Study-only")
        if self.deferred_reason in {"disabled", "paused"} and self.available_by is not None:
            raise ValueError("explicit disabled controls cannot promise an availability deadline")
        if self.interface_version == "0.2.1":
            if self.progress is not None or self.disposition == "introduced_again":
                raise ValueError("first-departure application evidence requires receipt interface 0.2.2")
            return self
        if self.progress is None or self.disposition not in {"introduced_again", "first_introduction"} or self.observation != "missed":
            raise ValueError("0.2.2 requires qualified missed-departure progress and its accepted disposition")
        progress = self.progress
        if self.disposition == "introduced_again" and progress.introduction_applied_at is not None:
            raise ValueError("introduced Again cannot claim first introduction")
        if self.disposition == "first_introduction" and progress.scheduling_review_id is not None:
            raise ValueError("first introduction cannot fabricate an Again scheduling review")
        if self.admission_status in {"available", "settled"}:
            application = progress.schedule_applied_at if self.disposition == "introduced_again" else progress.introduction_applied_at
            if application is None or progress.remediation_fence is None:
                raise ValueError("available consequence needs actual scheduling/admission evidence")
        if ((self.admission_status == "settled") != (progress.settlement is not None)
                or (progress.settlement is not None and self.status != "settled")
                or (self.status == "settled" and progress.settlement is None
                    and self.outcome not in {"withdrawn", "historical_preserved"})):
            raise ValueError("settled consequence requires its genuine post-application completion receipt")
        return self


class EncounterFeedbackPort(LearningFeedbackPort, Protocol):
    """Additive next-release capability; LearningFeedbackPort remains unchanged.

    Use one GameEffectKey/receipt ledger for both command kinds, shared with
    control_effect's pre-admission holds. Persist original source, detection,
    observation and owner disposition across replay/admission/recomparison.
    A previously unintroduced encounter must not later become a fake Again.
    Legacy receipts remain readable; applied_at alone proves no Again. New
    first-departure work uses qualified intake, current-state Again or canonical
    first introduction, with immutable first-acceptance disposition. Study revalidates current
    definition/mapping/state, owns fresh first introduction, and preserves active
    attempts. Real-game first introductions use the approved targeted exception
    to ordinary new-card allowance (including zero) and Study-only. Study must
    not change those global settings or extend the exception to unrelated cards.
    Explicit full pause, maintenance safety, source/identity/legality and conflict
    guards remain. Foreground activity cannot indefinitely starve bounded local
    preparation. A supplied payload digest is a claim, not owner authority.
    """
    def accept_encounter(self, encounter: GameEncounter) -> EncounterReceipt: ...
    def encounter_status(self, key: GameEffectKey) -> EncounterReceipt | None: ...


class QualifiedGameFeedbackPort(EncounterFeedbackPort, Protocol):
    """Same Study owner/ledger; original command plus separately prepared facts.

    Revalidate the witness through current owning authority; do not trust the
    policy label. Accept each key once, at current processing time/Study day.
    Introduced misses use the existing ordinary Again application; unseen
    misses retain first-introduction intent. Preserve issued attempts and require
    a genuine compatible post-reset/post-admission issuance for settlement.
    """
    def accept_qualified(self, command: GameEncounter | GameLapse, *,
                         qualification: FirstDepartureWitness) -> EncounterReceipt: ...


def withdrawal_only_delivery(delivery: EvidenceDelivery, withdrawal: EvidenceWithdrawal) -> EvidenceDelivery:
    """Reuse v3's existing withdrawal-only envelope, retaining explicit authority.

    The caller supplies fresh envelope identity/revision and actual captured
    authority. No lookup, fabricated graph authority or owner mutation occurs.
    """
    payload = delivery.model_dump(mode="json")
    payload.update(encounters=[], comparisons=[], misses=[], obligations=[], withdrawals=[withdrawal.model_dump(mode="json")])
    return EvidenceDelivery.model_validate_json(json.dumps(payload))
