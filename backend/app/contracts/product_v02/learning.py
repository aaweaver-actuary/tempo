"""Study-owned semantic operations. This module implements no scheduling or storage."""
import json
from typing import Literal, Protocol

from pydantic import ConfigDict, Field, field_validator, model_validator
from tempo_game_evidence_contracts.v2 import EvidenceWithdrawal
from tempo_game_evidence_contracts.v3 import EvidenceDelivery

from . import Digest, Identifier, Revision, UciMove, UtcTimestamp, Value
from .games import Finding
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
    """Receiver owns persistence, receipts, chronology, scheduler and <=48h deadline.

    accept_lapse: one effect per logical key. Duplicate cannot extend its deadline.
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


class GameEncounter(Value):
    """Next-release source facts, not a producer decision to introduce or lapse.

    Matched play is frequency evidence. Missed play remains importance evidence
    even before introduction or after later learning makes a lapse ineligible.
    Study revalidates the supplied current mapping and chooses any effect.
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
    queue availability. No deadline is promised under explicit disabled controls.
    """
    interface_version: Literal["0.2.1"] = "0.2.1"
    detected_at: UtcTimestamp
    source_digest: Digest
    observation: Literal["matched", "missed"]
    observation_recorded: bool
    disposition: Literal["review_priority", "first_introduction", "existing_lapse", "frequency_only"] | None
    admission_status: Literal["pending", "available", "settled", "not_requested"]
    deferred_reason: Literal["study_only", "disabled", "paused", "zero_limit", "introduction_allowance", "active_attempt", "authority_unavailable", "unknown_chronology"] | None

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
        if self.deferred_reason in {"study_only", "disabled", "paused", "zero_limit"} and self.available_by is not None:
            raise ValueError("explicit disabled controls cannot promise an availability deadline")
        return self


class EncounterFeedbackPort(LearningFeedbackPort, Protocol):
    """Additive next-release capability; LearningFeedbackPort remains unchanged.

    Use one GameEffectKey/receipt ledger for both command kinds, shared with
    control_effect's pre-admission holds. Persist original source, detection,
    observation and owner disposition across replay/admission/recomparison.
    A previously unintroduced encounter must not later become a fake Again.
    Historical misses may retain priority without reversing later progress;
    correct play is frequency, never invented failure. Study revalidates current
    definition/mapping/state, owns fresh first introduction, and preserves active
    attempts. Paused/disabled/zero-limit and introduction allowance decisions stay
    with Study. No reserve/exception policy or universal <=48h promise is chosen
    by this seam. A supplied payload digest is a claim, not owner authority.
    """
    def accept_encounter(self, encounter: GameEncounter) -> EncounterReceipt: ...
    def encounter_status(self, key: GameEffectKey) -> EncounterReceipt | None: ...


def withdrawal_only_delivery(delivery: EvidenceDelivery, withdrawal: EvidenceWithdrawal) -> EvidenceDelivery:
    """Reuse v3's existing withdrawal-only envelope, retaining explicit authority.

    The caller supplies fresh envelope identity/revision and actual captured
    authority. No lookup, fabricated graph authority or owner mutation occurs.
    """
    payload = delivery.model_dump(mode="json")
    payload.update(encounters=[], comparisons=[], misses=[], obligations=[], withdrawals=[withdrawal.model_dump(mode="json")])
    return EvidenceDelivery.model_validate_json(json.dumps(payload))
