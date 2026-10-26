"""Study-owned semantic operations. This module implements no scheduling or storage."""
import json
from typing import Literal, Protocol

from pydantic import ConfigDict, Field, field_validator, model_validator
from tempo_game_evidence_contracts.v2 import EvidenceWithdrawal
from tempo_game_evidence_contracts.v3 import EvidenceDelivery

from . import Digest, Identifier, Revision, UtcTimestamp, Value
from .games import Finding


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


def withdrawal_only_delivery(delivery: EvidenceDelivery, withdrawal: EvidenceWithdrawal) -> EvidenceDelivery:
    """Reuse v3's existing withdrawal-only envelope, retaining explicit authority.

    The caller supplies fresh envelope identity/revision and actual captured
    authority. No lookup, fabricated graph authority or owner mutation occurs.
    """
    payload = delivery.model_dump(mode="json")
    payload.update(encounters=[], comparisons=[], misses=[], obligations=[], withdrawals=[withdrawal.model_dump(mode="json")])
    return EvidenceDelivery.model_validate_json(json.dumps(payload))
