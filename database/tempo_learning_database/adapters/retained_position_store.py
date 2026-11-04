"""Representative non-serving retained position storage in a borrowed transaction.

No source reads, commits, scheduler invocation, or authority/serving transition.
Caller must provide a complete, independently established donor namespace check;
this adapter can reject a collision but cannot prove an inventory complete.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime

from psycopg.pq import TransactionStatus

from tempo_learning_transfer_contracts.v1 import (
    SourceManifest, StageReceipt, key_token, plan_transfer, record_digest,
)


class RetainedPositionConflict(ValueError):
    """Immutable identity or unsafe representative source."""


def _state(manifest, legacy_fsrs_ids):
    facts = [f for f in manifest.facts if f.source.store == "study_position_states"]
    if len(facts) != 1:
        raise RetainedPositionConflict("BLOCK: exactly one position state required")
    fact = facts[0]
    try:
        value = json.loads(base64.b64decode(fact.original_base64, validate=True))
        names = ("repertoire_id", "variant", "position", "expected_uci", "policy_version",
                 "owner_epoch", "source_digest", "version", "fsrs_internal_card_id", "fsrs_json",
                 "due_at", "introduced_at", "donor_card_id", "donor_card_revision",
                 "donor_fingerprint", "donor_selection_policy")
        if set(value) != set(names):
            raise ValueError("state field set")
        key = tuple(part.value for part in fact.source.primary_key)
        if key != tuple(value[n] for n in names[:3]) or value["variant"] != "standard":
            raise ValueError("position key")
        if any(not isinstance(value[n], str) or not value[n] for n in
               ("repertoire_id", "position", "expected_uci", "policy_version", "owner_epoch",
                "source_digest", "fsrs_json")):
            raise ValueError("missing fields")
        if any(type(value[n]) is not int or value[n] <= 0 for n in
               ("version", "fsrs_internal_card_id")):
            raise ValueError("invalid identity")
        fsrs = json.loads(value["fsrs_json"])
        if not isinstance(fsrs, dict) or type(fsrs.get("card_id")) is not int or fsrs["card_id"] != value["fsrs_internal_card_id"]:
            raise ValueError("ambiguous FSRS")
        donor_names = ("donor_card_id", "donor_card_revision", "donor_fingerprint", "donor_selection_policy")
        donor_values = tuple(value[n] for n in donor_names)
        if any(v is not None for v in donor_values):
            if (any(not isinstance(value[n], str) or not value[n] for n in
                    ("donor_card_id", "donor_fingerprint", "donor_selection_policy"))
                    or type(value["donor_card_revision"]) is not int):
                raise ValueError("partial or invalid donor")
            donors = [f for f in manifest.facts if f.source.store == "cards" and
                      len(f.source.primary_key) == 1 and f.source.primary_key[0].value == value["donor_card_id"]]
            if len(donors) != 1 or donors[0].source not in fact.references:
                raise ValueError("missing donor reference")
            donor = json.loads(base64.b64decode(donors[0].original_base64, validate=True))
            donor_fsrs = json.loads(donor["fsrs_card_json"])
            donor_id = donor_fsrs.get("card_id") if isinstance(donor_fsrs, dict) else None
            if (donor.get("id") != value["donor_card_id"] or donor.get("revision") != value["donor_card_revision"]
                    or type(donor_id) is not int or donor_id not in legacy_fsrs_ids):
                raise ValueError("ambiguous donor identity")
        if value["fsrs_internal_card_id"] in legacy_fsrs_ids:
            raise ValueError("reserved FSRS collision")
        for n in ("due_at", "introduced_at"):
            if value[n] is not None:
                parsed = datetime.fromisoformat(value[n])
                if parsed.utcoffset() is None:
                    raise ValueError("timezone required")
        if value["due_at"] is None:
            raise ValueError("due required")
        return tuple(value[n] for n in names), fact.original_digest
    except (ValueError, TypeError, KeyError, UnicodeDecodeError) as error:
        raise RetainedPositionConflict("BLOCK: invalid or ambiguous position donor/state: " + str(error)) from error


def stage_position(database, manifest: SourceManifest, receipt: StageReceipt, *, legacy_fsrs_ids: frozenset[int]):
    """Stage exactly one representative snapshot atomically under caller ownership."""
    # Revalidate even previously constructed Pydantic instances: model_copy can
    # bypass validators, including source completeness and reference checks.
    manifest = SourceManifest.model_validate(manifest.model_dump())
    receipt = StageReceipt.model_validate(receipt.model_dump())
    if database.info.transaction_status != TransactionStatus.INTRANS:
        raise RetainedPositionConflict("BLOCK: caller-owned active transaction required")
    if not isinstance(legacy_fsrs_ids, frozenset) or any(type(i) is not int or i <= 0 for i in legacy_fsrs_ids):
        raise RetainedPositionConflict("BLOCK: explicit legacy FSRS inventory required")
    plan = plan_transfer(manifest)
    if (manifest.scope != "representative" or plan.status != "staging_only"
            or any(f.disposition != "EXACT" or f.target_key is None for f in manifest.facts)
            or receipt.transfer_id != manifest.transfer_id or receipt.manifest_digest != record_digest(manifest)
            or receipt.batch_digest != record_digest(manifest)):
        raise RetainedPositionConflict("BLOCK: invalid staging binding")
    if any(tuple((p.name, p.codec, p.value) for p in f.source.primary_key) !=
           tuple((p.name, p.codec, p.value) for p in f.target_key.primary_key)
           or f.source.store != f.target_key.store for f in manifest.facts):
        raise RetainedPositionConflict("BLOCK: EXACT identity changed")
    state, digest = _state(manifest, legacy_fsrs_ids)
    # Advisory transaction lock serializes absent as well as existing receipt keys.
    database.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("retained-position:" + receipt.transfer_id + ":" + receipt.batch_id,))
    prior = database.execute("SELECT manifest_digest,batch_digest,target_generation,receipt_id,cursor FROM retained_position_batches WHERE transfer_id=%s AND batch_id=%s", (receipt.transfer_id, receipt.batch_id)).fetchone()
    expected = (receipt.manifest_digest, receipt.batch_digest, receipt.target_generation, receipt.receipt_id, receipt.cursor)
    if prior is not None:
        if tuple(prior) != expected:
            raise RetainedPositionConflict("immutable batch conflict")
        return receipt
    database.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ("retained-position-key:" + json.dumps(state[:3]),))
    existing = database.execute("SELECT source_fact_digest FROM retained_position_states WHERE repertoire_id=%s AND variant=%s AND position=%s", state[:3]).fetchone()
    if existing is not None:
        raise RetainedPositionConflict("immutable position identity conflict")
    database.execute("INSERT INTO retained_position_batches VALUES (%s,%s,%s,%s,%s,%s,%s)",
                     (receipt.transfer_id, receipt.batch_id, *expected))
    database.execute("INSERT INTO retained_position_states VALUES (" + ",".join(["%s"] * 17) + ")", (*state, digest))
    for fact in manifest.facts:
        database.execute("INSERT INTO retained_position_facts VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)",
            (key_token(fact.source), key_token(fact.target_key), fact.family, fact.codec,
             base64.b64decode(fact.original_base64), fact.original_digest,
             json.dumps([key_token(ref) for ref in fact.references]), receipt.transfer_id, receipt.batch_id))
    return receipt


def read_position(database, repertoire_id, variant, position):
    """Read the exact copied state; no eligibility or runtime authority implied."""
    return database.execute("SELECT * FROM retained_position_states WHERE repertoire_id=%s AND variant=%s AND position=%s",
                            (repertoire_id, variant, position)).fetchone()
