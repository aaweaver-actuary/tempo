import pytest
from pydantic import ValidationError
from tempo_game_evidence_contracts import v1
from .conftest import load_fixture, load_reference


def test_duplicate_game_evidence_and_new_delivery_identity_do_not_duplicate_obligations():
    receiver = load_reference().ReferenceEvidenceReceiver()
    delivery = v1.EvidenceDelivery.model_validate(load_fixture("delivery"))
    first = receiver.deliver(delivery)
    assert receiver.deliver(delivery) == first
    duplicate = receiver.deliver(
        delivery.model_copy(update={"delivery_id": "retry-delivery"})
    )
    assert duplicate.accepted_obligation_ids == ["obligation-a"]
    assert duplicate.learning_revision == first.learning_revision == 2
    assert len(receiver.obligations) == 1


def test_pending_evidence_does_not_claim_learning_completion():
    receiver = load_reference().ReferenceEvidenceReceiver()
    delivery = v1.EvidenceDelivery.model_validate(load_fixture("delivery"))
    pending = receiver.deliver(delivery, pending=True)
    assert pending.status == "accepted_pending" and pending.learning_revision is None
    assert receiver.obligations == {}
    assert receiver.deliver(delivery).status == "completed"
    assert len(receiver.obligations) == 1


def test_same_game_evidence_identity_conflicting_payload_is_atomic():
    receiver = load_reference().ReferenceEvidenceReceiver()
    data = load_fixture("delivery")
    receiver.deliver(v1.EvidenceDelivery.model_validate(data))
    data["encounters"][0]["observed_move"] = "c2c4"
    data["delivery_id"] = "new-delivery"
    with pytest.raises(ValueError, match="evidence revision conflict"):
        receiver.deliver(v1.EvidenceDelivery.model_validate(data))
    assert len(receiver.obligations) == 1 and receiver.learning_revision == 2


def test_miss_and_obligation_lineage_must_match_observation():
    data = load_fixture("delivery")
    data["misses"][0]["encounter_id"] = "unobserved"
    with pytest.raises(ValidationError, match="unknown encounter"):
        v1.EvidenceDelivery.model_validate(data)
    data = load_fixture("delivery")
    data["obligations"][0]["source_revision"] = 2
    with pytest.raises(ValidationError, match="lineage"):
        v1.EvidenceDelivery.model_validate(data)


def test_completed_evidence_replay_cannot_regress_to_pending():
    receiver = load_reference().ReferenceEvidenceReceiver()
    delivery = v1.EvidenceDelivery.model_validate(load_fixture("delivery"))
    completed = receiver.deliver(delivery)
    assert receiver.deliver(delivery, pending=True) == completed
    new_delivery = delivery.model_copy(update={"delivery_id": "redelivery"})
    assert receiver.deliver(new_delivery, pending=True).status == "completed"
    assert len(receiver.obligations) == 1
