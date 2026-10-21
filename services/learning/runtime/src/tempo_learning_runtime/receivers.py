"""Versioned intake and required settlement under the sole Learning transaction."""

from tempo_learning_database.adapters import evidence_receipts as storage
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
    historical = storage.historical_delivery_receipt(database, delivery.delivery_id)
    if historical:
        if (
            historical[0]
            != hashlib.sha256(delivery.model_dump_json().encode()).hexdigest()
        ):
            raise HTTPException(409, "Evidence delivery identity reused")
        return json.loads(historical[1])
    frontier = storage.accepted_revision_frontier(database, workspace_id, delivery.evidence_id)
    from .remediation_adapter import receive as receive_remediation

    acknowledgement = receive_remediation(database, delivery, background=background,
        historical_evidence_revision=frontier[0] if frontier else None)
    if acknowledgement.status == "conflict" and acknowledgement.reason == "historical_authority_not_converted":
        raise HTTPException(409,
            "Historical obligation authority has not been converted; send a newer revision")
    if delivery.contract_version == 1 and acknowledgement.status == "conflict":
        raise HTTPException(409, acknowledgement.reason)
    return acknowledgement.model_dump(mode="json")
