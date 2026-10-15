"""Receiver and settlement registration share the runtime's sole transaction/admission."""

import hashlib, json
from fastapi import HTTPException
from tempo_game_evidence_contracts.v1 import EvidenceDelivery, EvidenceAcknowledgement
from .admission import admit
from .real_game_feedback import clear_satisfied_miss_priority, MISS_REASON
from .clock import date

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


def settle(database, card_id, review_id):
    if _settlement:
        _settlement(database, card_id, review_id)
    else:
        clear_satisfied_miss_priority(database, card_id)


def receive(database, delivery, workspace_id):
    delivery = EvidenceDelivery.model_validate(delivery)
    if delivery.workspace_id != workspace_id:
        raise HTTPException(403, "Evidence belongs to another workspace")
    if _receiver:
        return _receiver(database, delivery, admit).model_dump(mode="json")
    serialized = delivery.model_dump_json()
    digest = hashlib.sha256(serialized.encode()).hexdigest()
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("learning:evidence:" + delivery.evidence_id,),
    )
    prior = database.execute(
        "SELECT digest,result_json FROM evidence_deliveries WHERE delivery_id=?",
        (delivery.delivery_id,),
    ).fetchone()
    if prior:
        if prior[0] != digest:
            raise HTTPException(409, "Evidence delivery identity reused")
        return json.loads(prior[1])
    for obligation in delivery.obligations:
        if not database.execute(
            "SELECT 1 FROM cards WHERE id=? AND publication_generation=? AND serving_active=1",
            (obligation.card_id, obligation.publication_generation),
        ).fetchone():
            raise HTTPException(409, "Evidence names a stale executable definition")
        encoded = obligation.model_dump_json()
        existing = database.execute(
            "SELECT payload_json FROM learning_obligations WHERE obligation_id=?",
            (obligation.obligation_id,),
        ).fetchone()
        if existing and existing[0] != encoded:
            raise HTTPException(409, "Obligation identity reused")
        database.execute(
            "INSERT INTO learning_obligations(obligation_id,source_evidence_id,source_revision,card_id,decision_id,publication_generation,kind,payload_json) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            (
                obligation.obligation_id,
                obligation.source_evidence_id,
                obligation.source_revision,
                obligation.card_id,
                obligation.decision_id,
                obligation.publication_generation,
                obligation.kind,
                encoded,
            ),
        )
        if obligation.kind == "real_game_miss":
            admit(database, obligation.card_id, date.today().isoformat(), MISS_REASON)
    result = EvidenceAcknowledgement(
        contract_version=1,
        workspace_id=workspace_id,
        delivery_id=delivery.delivery_id,
        evidence_id=delivery.evidence_id,
        evidence_revision=delivery.evidence_revision,
        status="completed",
        accepted_obligation_ids=[item.obligation_id for item in delivery.obligations],
        learning_revision=delivery.evidence_revision,
        reason=None,
    ).model_dump(mode="json")
    database.execute(
        "INSERT INTO evidence_deliveries VALUES(?,?,?)",
        (delivery.delivery_id, digest, json.dumps(result)),
    )
    return result
