"""Versioned intake and required settlement under the sole Learning transaction."""

from typing import Annotated
import hashlib, json
from fastapi import HTTPException
from pydantic import Field, TypeAdapter
from tempo_game_evidence_contracts import v1, v2
from .admission import admit

EvidenceDelivery = Annotated[
    v1.EvidenceDelivery | v2.EvidenceDelivery, Field(discriminator="contract_version")
]
_delivery_adapter = TypeAdapter(EvidenceDelivery)
_receiver = None
_settlement = None


def register_evidence_receiver(handler):
    global _receiver
    if _receiver is not None:
        raise ValueError("An evidence receiver already owns this runtime")
    _receiver = handler


def register_settlement(handler):
    global _settlement
    if _settlement is not None:
        raise ValueError("A required settlement handler already owns this runtime")
    _settlement = handler


def settle(database, card_id, review_id, **context):
    if _settlement:
        # Retain the existing three-argument registration contract.
        _settlement(database, card_id, review_id)
    else:
        from .remediation_adapter import settle_review

        settle_review(database, card_id, review_id, **context)


def receive(database, delivery, workspace_id, *, background=False):
    delivery = _delivery_adapter.validate_python(delivery)
    if delivery.workspace_id != workspace_id:
        raise HTTPException(403, "Evidence belongs to another workspace")
    if _receiver:
        return _receiver(database, delivery, admit).model_dump(mode="json")
    # Legacy receipts are immutable read-only compatibility evidence. They never
    # execute the retired obligation writer or fabricate missing provenance.
    historical = database.execute(
        "SELECT digest,result_json FROM evidence_deliveries WHERE delivery_id=?",
        (delivery.delivery_id,),
    ).fetchone()
    if historical:
        if (
            historical[0]
            != hashlib.sha256(delivery.model_dump_json().encode()).hexdigest()
        ):
            raise HTTPException(409, "Evidence delivery identity reused")
        return json.loads(historical[1])
    recorded = database.execute_native(
        "SELECT 1 FROM remediation_deliveries WHERE workspace_id=%s AND delivery_id=%s",
        (workspace_id, delivery.delivery_id),
    ).fetchone()
    frontier = database.execute(
        "SELECT evidence_revision FROM accepted_evidence_revisions WHERE workspace_id=? AND evidence_id=?",
        (workspace_id, delivery.evidence_id),
    ).fetchone()
    if recorded is None and frontier and delivery.evidence_revision <= frontier[0]:
        if delivery.evidence_revision == frontier[0]:
            raise HTTPException(
                409,
                "Historical obligation authority has not been converted; send a newer revision",
            )
        model = (
            v2.EvidenceAcknowledgement
            if delivery.contract_version == 2
            else v1.EvidenceAcknowledgement
        )
        return model(
            contract_version=delivery.contract_version,
            workspace_id=workspace_id,
            delivery_id=delivery.delivery_id,
            evidence_id=delivery.evidence_id,
            evidence_revision=delivery.evidence_revision,
            status="stale_revision",
            reason="source_revision_superseded",
            accepted_obligation_ids=[],
            learning_revision=None,
        ).model_dump(mode="json")
    # Preserve v1's transport-first stale acknowledgement behavior while the
    # remediation repository remains the only writer. V2 retains its richer
    # immutable historical-semantic replay policy.
    if delivery.contract_version == 1:
        source = database.execute_native(
            "SELECT evidence_revision FROM remediation_source_heads WHERE workspace_id=%s AND evidence_id=%s",
            (workspace_id, delivery.evidence_id),
        ).fetchone()
        if recorded is None and source and delivery.evidence_revision < source[0]:
            return v1.EvidenceAcknowledgement(
                contract_version=1,
                workspace_id=workspace_id,
                delivery_id=delivery.delivery_id,
                evidence_id=delivery.evidence_id,
                evidence_revision=delivery.evidence_revision,
                status="stale_revision",
                reason="source_revision_superseded",
                accepted_obligation_ids=[],
                learning_revision=None,
            ).model_dump(mode="json")
    from .remediation_adapter import receive as receive_remediation

    acknowledgement = receive_remediation(database, delivery, background=background)
    if delivery.contract_version == 1 and acknowledgement.status == "conflict":
        raise HTTPException(409, acknowledgement.reason)
    return acknowledgement.model_dump(mode="json")
