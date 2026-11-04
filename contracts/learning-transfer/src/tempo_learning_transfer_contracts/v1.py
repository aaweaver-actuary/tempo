"""V1 retained-history wire records. Pure validation; no storage or activation.

Evidence references are claims until the owning native/browser verifier reads
them. These DTOs cannot confer authority or certify a live migration.
"""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1)]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Family = Literal["content", "cards_history", "position", "issued", "queue", "games", "tactics", "browser"]
FAMILIES = {"content", "cards_history", "position", "issued", "queue", "games", "tactics", "browser"}


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class KeyPart(Record):
    name: Identifier
    codec: Identifier
    value: str  # Exact encoded value; BIGINT is never a floating-point JSON ID.


class SourceKey(Record):
    authority: Identifier
    store: Identifier
    primary_key: tuple[KeyPart, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_columns(self):
        names = [part.name for part in self.primary_key]
        if len(set(names)) != len(names):
            raise ValueError("BLOCK: duplicate primary-key column")
        return self


class StoreShape(Record):
    authority: Identifier
    store: Identifier
    # Ordered complete source-schema PK columns and canonical codecs, approved
    # by that storage owner. Values remain in SourceKey only.
    primary_key: tuple[tuple[Identifier, Identifier], ...] = Field(min_length=1)
    schema_evidence: Identifier


class CaptureInventory(Record):
    shape: StoreShape
    expected_count: Annotated[int, Field(ge=0)]
    expected_digest: Digest
    completeness_evidence: Identifier


class StateFact(Record):
    family: Family
    source: SourceKey
    codec: Identifier
    original_base64: str
    original_digest: Digest
    references: tuple[SourceKey, ...]
    required: bool
    disposition: Literal["EXACT", "TRANSLATE", "ARCHIVE", "BLOCK"]
    target_owner: Identifier
    target_key: SourceKey | None
    translation_proof: Identifier | None
    archive_readback: Identifier | None
    blocker: Identifier | None

    @model_validator(mode="after")
    def preservation(self):
        try:
            raw = base64.b64decode(self.original_base64, validate=True)
        except ValueError as error:
            raise ValueError("BLOCK: invalid original bytes") from error
        if digest_bytes(raw) != self.original_digest:
            raise ValueError("BLOCK: changed original bytes/digest")
        if self.required and self.disposition == "ARCHIVE":
            raise ValueError("BLOCK: required work cannot be archived")
        if self.disposition in {"EXACT", "TRANSLATE"} and self.target_key is None:
            raise ValueError("BLOCK: missing target identity")
        if self.disposition == "TRANSLATE" and self.translation_proof is None:
            raise ValueError("BLOCK: lossless bijection/inverse proof required")
        if self.disposition == "ARCHIVE" and self.archive_readback is None:
            raise ValueError("BLOCK: archive readback required")
        if self.disposition == "BLOCK" and self.blocker is None:
            raise ValueError("BLOCK: explicit unresolved reason required")
        return self


class SourceManifest(Record):
    version: Literal[1]
    transfer_id: Identifier
    source_authority: Identifier
    target_authority: Identifier
    source_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    source_schema: Digest
    target_schema: Digest
    source_watermark: Identifier  # Owner-defined consistent snapshot, not wall time.
    scope: Literal["representative", "complete"]
    source_inventory: tuple[CaptureInventory, ...] = Field(min_length=1)
    target_shapes: tuple[StoreShape, ...] = Field(min_length=1)
    family_evidence: tuple[tuple[Family, Identifier], ...]
    capture: Literal["warm", "final_fenced"]
    fence_id: Identifier | None
    study_timezone: Identifier
    scheduler_identity: Identifier  # Algorithm/version/settings/weights digest reference.
    family_inventory: tuple[Family, ...]
    unresolved: tuple[Identifier, ...]
    facts: tuple[StateFact, ...]

    @model_validator(mode="after")
    def identities(self):
        ZoneInfo(self.study_timezone)
        if self.source_authority == self.target_authority:
            raise ValueError("BLOCK: distinct staged target authority required")
        if set(self.family_inventory) != FAMILIES or len(self.family_inventory) != len(FAMILIES):
            raise ValueError("BLOCK: all state families must be inventoried, including empty families")
        if self.scope == "complete" and (len(self.family_evidence) != len(FAMILIES)
                or {family for family, _ in self.family_evidence} != FAMILIES):
            raise ValueError("BLOCK: complete capture needs each family's completeness evidence")
        if (self.capture == "final_fenced") != (self.fence_id is not None):
            raise ValueError("BLOCK: final capture must bind source fence")
        keys = [key_token(fact.source) for fact in self.facts]
        if len(keys) != len(set(keys)):
            raise ValueError("BLOCK: duplicate source identity")
        known = set(keys)
        targets = []
        source_shapes = {(item.shape.authority, item.shape.store): item.shape for item in self.source_inventory}
        target_shapes = {(item.authority, item.store): item for item in self.target_shapes}
        if len(source_shapes) != len(self.source_inventory) or len(target_shapes) != len(self.target_shapes):
            raise ValueError("BLOCK: duplicate store inventory")
        for fact in self.facts:
            validate_key(fact.source, source_shapes)
            if any(key_token(ref) not in known for ref in fact.references):
                raise ValueError("BLOCK: unresolved source reference")
            if fact.target_key is not None:
                validate_key(fact.target_key, target_shapes)
                if fact.target_key.authority != self.target_authority:
                    raise ValueError("BLOCK: target authority mismatch")
                targets.append(key_token(fact.target_key))
        if len(targets) != len(set(targets)):
            raise ValueError("BLOCK: target identity collision; explicit grouped mapping required")
        for inventory in self.source_inventory:
            selected = tuple(fact for fact in self.facts if
                             (fact.source.authority, fact.source.store) ==
                             (inventory.shape.authority, inventory.shape.store))
            if len(selected) != inventory.expected_count or facts_digest(selected) != inventory.expected_digest:
                raise ValueError("BLOCK: incomplete store capture count/digest")
        return self


class TransferPlan(Record):
    version: Literal[1]
    transfer_id: Identifier
    manifest_digest: Digest
    mapping_version: Identifier
    status: Literal["staging_only", "BLOCK"]
    blockers: tuple[Identifier, ...]


class StageReceipt(Record):
    version: Literal[1]
    transfer_id: Identifier
    manifest_digest: Digest
    batch_id: Identifier
    batch_digest: Digest
    cursor: str | None
    target_generation: Identifier
    receipt_id: Identifier


class VerificationReceipt(Record):
    version: Literal[1]
    transfer_id: Identifier
    manifest_digest: Digest
    target_generation: Identifier
    verifier_evidence: Identifier
    status: Literal["verified", "BLOCK"]
    blockers: tuple[Identifier, ...]


class FencingProof(Record):
    version: Literal[1]
    transfer_id: Identifier
    fence_id: Identifier
    source_authority: Identifier
    target_authority: Identifier
    source_watermark: Identifier
    denied_writer_inventory_digest: Digest
    stale_session_denial: Identifier
    credential_denial: Identifier
    restart_denial: Identifier
    lock_identity: Identifier
    evidence: Identifier


class ActivationReceipt(Record):
    version: Literal[1]
    transfer_id: Identifier
    manifest_digest: Digest
    fence_id: Identifier
    target_generation: Identifier
    target_authority: Identifier
    activation_id: Identifier
    target_acceptance: Literal["none", "accepted", "uncertain"]


class RuntimeProof(Record):
    version: Literal[1]
    activation_id: Identifier
    source_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    images: tuple[Digest, ...] = Field(min_length=3, max_length=3)
    schema_identity: Digest
    workspace: Identifier
    worker_identity: Identifier
    modes: tuple[Identifier, ...]
    scope: Literal["disposable", "installed"]
    fixture_evidence: tuple[Identifier, ...]
    one_writer_evidence: Identifier
    saved_review_playable_next_evidence: Identifier


def digest_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def record_digest(record: Record) -> str:
    """V1 envelope encoding only; original payload bytes are separately hashed."""
    return digest_bytes(json.dumps(record.model_dump(mode="json"), sort_keys=True,
                                   separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def key_token(key: SourceKey) -> str:
    # PK tuple order is canonical and owned by the source adapter.
    return json.dumps(key.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def validate_key(key: SourceKey, shapes: dict[tuple[str, str], StoreShape]) -> None:
    shape = shapes.get((key.authority, key.store))
    if shape is None or tuple((part.name, part.codec) for part in key.primary_key) != shape.primary_key:
        raise ValueError("BLOCK: full canonical primary key/codec required from storage owner")


def facts_digest(facts: tuple[StateFact, ...]) -> str:
    pairs = sorted((key_token(fact.source), fact.original_digest) for fact in facts)
    return digest_bytes(json.dumps(pairs, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def plan_transfer(manifest: SourceManifest) -> TransferPlan:
    blockers = tuple(manifest.unresolved) + tuple(
        fact.blocker for fact in manifest.facts if fact.disposition == "BLOCK"
    )
    return TransferPlan(version=1, transfer_id=manifest.transfer_id,
                        manifest_digest=record_digest(manifest), mapping_version="exact-v1",
                        status="BLOCK" if blockers else "staging_only", blockers=blockers)


def replay_batch(original: StageReceipt, proposed: StageReceipt) -> StageReceipt:
    if original != proposed:
        raise ValueError("immutable batch conflict")
    return original


def activation_blockers(manifest: SourceManifest, verified: VerificationReceipt,
                        fence: FencingProof, proposed: ActivationReceipt) -> tuple[str, ...]:
    """Check claimed bindings only. Native owners must verify evidence themselves."""
    blockers = list(plan_transfer(manifest).blockers)
    if manifest.scope != "complete":
        blockers.append("complete_capture_required")
    if manifest.capture != "final_fenced":
        blockers.append("final_fenced_capture_required")
    if (verified.status != "verified" or verified.blockers
            or verified.transfer_id != manifest.transfer_id
            or verified.manifest_digest != record_digest(manifest)):
        blockers.append("final_verification_mismatch")
    if (fence.transfer_id != manifest.transfer_id or fence.fence_id != manifest.fence_id
            or fence.source_authority != manifest.source_authority
            or fence.target_authority != manifest.target_authority
            or fence.source_watermark != manifest.source_watermark):
        blockers.append("source_fence_watermark_mismatch")
    if (proposed.transfer_id != manifest.transfer_id
            or proposed.manifest_digest != verified.manifest_digest
            or proposed.fence_id != fence.fence_id
            or proposed.target_generation != verified.target_generation
            or proposed.target_authority != manifest.target_authority):
        blockers.append("activation_binding_mismatch")
    return tuple(blockers)


def recovery_path(target_acceptance: Literal["none", "accepted", "uncertain"],
                  original_return_proven: bool) -> str:
    if target_acceptance not in {"none", "accepted", "uncertain"}:
        raise ValueError("BLOCK: unknown target commit status")
    if target_acceptance == "none" and original_return_proven:
        return "verified_original_return"
    return "forward_repair"


PUBLIC_MODELS = (
    SourceManifest, TransferPlan, StageReceipt, VerificationReceipt,
    FencingProof, ActivationReceipt, RuntimeProof,
)


def validate_contract(model: type[Record], payload: str | bytes) -> Record:
    return model.model_validate_json(payload)


def serialize_contract(value: Record) -> str:
    """The unchanged v1 envelope encoding used by record_digest."""
    return json.dumps(value.model_dump(mode="json"), sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False)


__all__ = [
    "Identifier", "Digest", "Family", "FAMILIES", "Record", "KeyPart",
    "SourceKey", "StoreShape", "CaptureInventory", "StateFact",
    "SourceManifest", "TransferPlan", "StageReceipt", "VerificationReceipt",
    "FencingProof", "ActivationReceipt", "RuntimeProof", "digest_bytes",
    "record_digest", "key_token", "validate_key", "facts_digest",
    "plan_transfer", "replay_batch", "activation_blockers", "recovery_path",
    "PUBLIC_MODELS", "validate_contract", "serialize_contract",
]
