"""W01 pure wire regressions; F01 mechanism input, not native parity proof."""
import base64
import json

import pytest
from pydantic import ValidationError

from tempo_learning_transfer_contracts.v1 import (
    FAMILIES, ActivationReceipt, CaptureInventory, FencingProof, KeyPart, SourceKey, SourceManifest, StageReceipt, StoreShape,
    StateFact, VerificationReceipt, activation_blockers, digest_bytes,
    facts_digest, plan_transfer, record_digest, recovery_path, replay_batch,
)

SHA = "5d879ffce599af7a2eaf9627e9e1c7ea82e9af5a"
DIGEST = "sha256:" + "1" * 64


def example():
    def key(store, parts, authority="synthetic-legacy"):
        return SourceKey(authority=authority, store=store,
                         primary_key=tuple(KeyPart(name=name, codec="text", value=value)
                                           for name, value in parts))
    position = (("repertoire_id", "synthetic-r"), ("variant", "standard"),
                ("position", "synthetic-exact-position-token"))
    state_key = key("study_position_states", position)
    # Serialized scheduler bytes include significant whitespace. They are never
    # decoded/replayed to manufacture an original schedule by this contract.
    fsrs = '{ "card_id": 4503599627370496, "due": "2026-11-05T01:02:03.123456+00:00", "stability": 12.75 }'
    state = json.dumps({"fsrs_json": fsrs, "owner_epoch": "epoch-a", "version": 4,
                        "due_at": "2026-11-05T01:02:03.123456+00:00"}).encode()
    reviews = [("review-original-1", "2026-10-01T12:00:00Z", None),
               ("review-original-2", "2026-10-02T12:00:00Z", "2026-10-03T12:00:00Z")]
    facts = []
    for source, payload, refs, family in [
        (state_key, state, (), "position"),
        *[(key("reviews", (("id", ident),)),
           json.dumps({"id": ident, "reviewed_at": clock, "invalidated_at": invalid}).encode(),
           (state_key,), "cards_history") for ident, clock, invalid in reviews],
    ]:
        facts.append(StateFact(family=family, source=source, codec="synthetic-json-utf8-v1",
                               original_base64=base64.b64encode(payload).decode(),
                               original_digest=digest_bytes(payload), references=refs,
                               required=True, disposition="EXACT", target_owner="Learning",
                               target_key=source.model_copy(update={"authority": "synthetic-learning"}),
                               translation_proof=None, archive_readback=None, blocker=None))
    shapes = {}
    for fact in facts:
        for source in (fact.source, fact.target_key):
            shapes[(source.authority, source.store)] = StoreShape(
                authority=source.authority, store=source.store,
                primary_key=tuple((part.name, part.codec) for part in source.primary_key),
                schema_evidence="synthetic-schema-shape")
    inventory = tuple(CaptureInventory(shape=shape,
                       expected_count=len(selected := tuple(f for f in facts if f.source.store == shape.store)),
                       expected_digest=facts_digest(selected), completeness_evidence="synthetic-capture")
                      for shape in shapes.values() if shape.authority == "synthetic-legacy")
    return SourceManifest(version=1, transfer_id="synthetic-transfer-1",
                          source_authority="synthetic-legacy", target_authority="synthetic-learning",
                          source_sha=SHA, source_schema=DIGEST, target_schema=DIGEST,
                          source_watermark="synthetic-post-fence-snapshot", capture="final_fenced",
                          scope="representative", source_inventory=inventory,
                          target_shapes=tuple(shape for shape in shapes.values() if shape.authority == "synthetic-learning"),
                          family_evidence=(),
                          fence_id="synthetic-fence", study_timezone="America/New_York",
                          scheduler_identity="fsrs-6.3.2-settings-unverified-synthetic",
                          family_inventory=tuple(sorted(FAMILIES)), unresolved=(), facts=tuple(facts))


def verification(manifest):
    return VerificationReceipt(version=1, transfer_id=manifest.transfer_id,
                               manifest_digest=record_digest(manifest), target_generation="stage-1",
                               verifier_evidence="synthetic-verifier-not-runtime-proof",
                               status="verified", blockers=())


def fence(manifest):
    return FencingProof(version=1, transfer_id=manifest.transfer_id,
                        fence_id="synthetic-fence", source_authority=manifest.source_authority,
                        target_authority=manifest.target_authority,
                        source_watermark=manifest.source_watermark,
                        denied_writer_inventory_digest=DIGEST, stale_session_denial="synthetic-session",
                        credential_denial="synthetic-role", restart_denial="synthetic-restart",
                        lock_identity="synthetic-original-lock", evidence="synthetic-fence-evidence")


def proposed(manifest):
    return ActivationReceipt(version=1, transfer_id=manifest.transfer_id,
                             manifest_digest=record_digest(manifest), fence_id="synthetic-fence",
                             target_generation="stage-1", target_authority=manifest.target_authority,
                             activation_id="synthetic-activation", target_acceptance="none")


def test_exact_original_bytes_and_large_fsrs_id_round_trip():
    manifest = example()
    restored = SourceManifest.model_validate_json(manifest.model_dump_json())
    assert restored == manifest
    assert record_digest(restored) == record_digest(manifest)
    payload = json.loads(base64.b64decode(restored.facts[0].original_base64))
    assert payload["fsrs_json"].startswith('{ "card_id": 4503599627370496, ')
    assert "123456+00:00" in payload["due_at"]
    assert len(restored.facts) == 3
    assert plan_transfer(restored).status == "staging_only"


@pytest.mark.parametrize("change", ["missing", "unknown", "bytes", "reference", "collision", "archive", "translation", "pk", "order", "codec", "omission"])
def test_invalid_or_ambiguous_identity_blocks(change):
    data = json.loads(example().model_dump_json())
    if change == "missing":
        del data["source_authority"]
    elif change == "unknown":
        data["unknown_authority_field"] = "guess"
    elif change == "bytes":
        data["facts"][0]["original_base64"] = base64.b64encode(b"changed").decode()
    elif change == "reference":
        data["facts"][1]["references"][0]["store"] = "unknown"
    elif change == "collision":
        data["facts"][1]["target_key"] = data["facts"][0]["target_key"]
    elif change == "archive":
        data["facts"][0]["disposition"] = "ARCHIVE"
    elif change == "translation":
        data["facts"][0]["disposition"] = "TRANSLATE"
    elif change == "pk":
        data["facts"][0]["source"]["primary_key"].pop()
    elif change == "order":
        data["facts"][0]["target_key"]["primary_key"].reverse()
    elif change == "codec":
        data["facts"][0]["target_key"]["primary_key"][0]["codec"] = "alias"
    else:
        data["facts"].pop()
    with pytest.raises(ValidationError):
        SourceManifest.model_validate_json(json.dumps(data))


def test_explicit_unknown_is_a_blocked_plan():
    manifest = example().model_copy(update={"unresolved": ("installed-device-authority-UNKNOWN",)})
    assert plan_transfer(manifest).status == "BLOCK"
    assert activation_blockers(manifest, verification(manifest), fence(manifest), proposed(manifest))


def test_same_batch_replays_original_receipt_changed_digest_conflicts():
    receipt = StageReceipt(version=1, transfer_id="transfer", manifest_digest=DIGEST,
                           batch_id="original-batch", batch_digest=DIGEST, cursor=None,
                           target_generation="stage", receipt_id="original-receipt")
    assert replay_batch(receipt, StageReceipt.model_validate_json(receipt.model_dump_json())) is receipt
    with pytest.raises(ValueError, match="conflict"):
        replay_batch(receipt, receipt.model_copy(update={"batch_digest": "sha256:" + "2" * 64}))


def test_warm_snapshot_or_post_fence_delta_cannot_activate():
    final = example()
    assert activation_blockers(final, verification(final), fence(final), proposed(final)) == ("complete_capture_required",)
    warm = final.model_copy(update={"capture": "warm", "fence_id": None})
    assert "final_fenced_capture_required" in activation_blockers(warm, verification(warm), fence(final), proposed(warm))
    changed = final.model_copy(update={"source_watermark": "later-snapshot"})
    assert set(activation_blockers(changed, verification(final), fence(final), proposed(final))) == {
        "complete_capture_required", "final_verification_mismatch", "source_fence_watermark_mismatch"}


def test_proposed_generation_must_equal_verified_generation():
    manifest = example()
    receipt = proposed(manifest).model_copy(update={"target_generation": "unverified-stage-2"})
    assert "activation_binding_mismatch" in activation_blockers(manifest, verification(manifest), fence(manifest), receipt)


def test_empty_family_names_do_not_certify_complete_capture():
    data = json.loads(example().model_dump_json())
    data["scope"] = "complete"
    with pytest.raises(ValidationError, match="completeness evidence"):
        SourceManifest.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("status,proven,expected", [
    ("none", True, "verified_original_return"), ("none", False, "forward_repair"),
    ("accepted", True, "forward_repair"), ("uncertain", True, "forward_repair"),
])
def test_recovery_never_reverts_new_or_uncertain_target_effect(status, proven, expected):
    assert recovery_path(status, proven) == expected
