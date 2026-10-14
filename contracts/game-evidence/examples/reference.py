"""Learning-side delivery illustration; does not write a queue or schedule."""

from tempo_game_evidence_contracts import v1


class ReferenceEvidenceReceiver:
    def __init__(self):
        self.deliveries = {}
        self.evidence = {}
        self.obligations = {}
        self.learning_revision = 1
        self.completed_evidence = {}

    def deliver(self, delivery: v1.EvidenceDelivery, *, pending=False):
        delivery_key = (delivery.workspace_id, delivery.delivery_id)
        content = delivery.model_dump(exclude={"delivery_id"})
        existing_delivery = self.deliveries.get(delivery_key)
        if existing_delivery and existing_delivery != content:
            raise ValueError("delivery payload conflict")
        evidence_key = (
            delivery.workspace_id,
            delivery.evidence_id,
            delivery.evidence_revision,
        )
        if evidence_key in self.evidence and self.evidence[evidence_key] != content:
            raise ValueError("evidence revision conflict")
        additions = {}
        for obligation in delivery.obligations:
            key = (delivery.workspace_id, obligation.obligation_id)
            payload = obligation.model_dump()
            if key in self.obligations and self.obligations[key] != payload:
                raise ValueError("obligation payload conflict")
            additions[key] = payload
        self.deliveries[delivery_key] = content
        self.evidence[evidence_key] = content
        if evidence_key in self.completed_evidence:
            pending = False
        if not pending:
            new_keys = additions.keys() - self.obligations.keys()
            self.obligations.update(additions)
            if new_keys:
                self.learning_revision += 1
            self.completed_evidence.setdefault(evidence_key, self.learning_revision)
        return v1.EvidenceAcknowledgement(
            contract_version=1,
            workspace_id=delivery.workspace_id,
            delivery_id=delivery.delivery_id,
            evidence_id=delivery.evidence_id,
            evidence_revision=delivery.evidence_revision,
            status="accepted_pending" if pending else "completed",
            accepted_obligation_ids=[
                item.obligation_id for item in delivery.obligations
            ],
            learning_revision=None
            if pending
            else self.completed_evidence[evidence_key],
            reason="awaiting_learning_commit" if pending else None,
        )
