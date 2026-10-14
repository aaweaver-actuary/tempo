from copy import deepcopy
import pytest
from pydantic import ValidationError
from tempo_content_publication_contracts import v1
from .conftest import load_fixture, load_reference


def example():
    reference = load_reference()
    receiver = reference.ReferenceActivation()
    publication = v1.Publication.model_validate(load_fixture("publication"))
    request = v1.ActivationRequest.model_validate(load_fixture("activation-request"))
    return reference, receiver, publication, request


def test_publication_acceptance_is_not_activation():
    _, receiver, publication, request = example()
    assert receiver.accept(publication) == "accepted_pending"
    assert receiver.active is None
    acknowledgement = receiver.activate(request)
    assert acknowledgement.status == "activated"
    assert receiver.active == ("workspace-a", "publication-a", 1)
    assert receiver.activate(request) == acknowledgement
    assert receiver.activation_revision == 2


def test_stale_generation_and_acknowledgement_cannot_activate_older_content():
    reference, receiver, publication, request = example()
    receiver.accept(publication)
    old_acknowledgement = receiver.activate(request)
    newer_data = publication.model_dump()
    newer_data["publication_generation"] = 2
    newer_data["definitions"][0]["publication_generation"] = 2
    receiver.accept(v1.Publication.model_validate(newer_data))
    new_request = request.model_copy(
        update={
            "activation_id": "activation-b",
            "publication_generation": 2,
            "expected_activation_revision": 2,
        }
    )
    assert receiver.activate(new_request).status == "activated"
    assert not reference.observe_activation(request, old_acknowledgement, 2, 3)
    assert (
        receiver.activate(
            request.model_copy(
                update={
                    "activation_id": "activation-stale",
                    "expected_activation_revision": 3,
                }
            )
        ).status
        == "stale_generation"
    )


def test_retirement_acknowledgement_requires_fencing_and_is_idempotent():
    _, receiver, publication, request = example()
    receiver.accept(publication)
    receiver.activate(request)
    retirement = v1.RetirementRequest.model_validate(load_fixture("retirement-request"))
    reply = receiver.retire(retirement)
    assert reply.status == "retired" and reply.stale_attempts_fenced
    assert receiver.active is None
    assert receiver.retire(retirement) == reply
    assert receiver.activation_revision == 3
    assert (
        receiver.activate(
            request.model_copy(
                update={
                    "activation_id": "retry-activation",
                    "expected_activation_revision": 3,
                }
            )
        ).status
        == "stale_generation"
    )
    with pytest.raises(ValidationError):
        v1.RetirementAcknowledgement.model_validate(
            {**reply.model_dump(), "stale_attempts_fenced": False}
        )


def test_structural_replacement_requires_accepted_current_generation():
    _, receiver, publication, request = example()
    receiver.accept(publication)
    receiver.activate(request)
    retirement = v1.RetirementRequest.model_validate(
        {
            **load_fixture("retirement-request"),
            "replacement_publication_id": "replacement",
            "replacement_generation": 1,
        }
    )
    assert receiver.retire(retirement).status == "rejected"
    assert receiver.active == ("workspace-a", "publication-a", 1)
    replacement = publication.model_dump()
    replacement["publication_id"] = "replacement"
    replacement["definitions"][0]["publication_id"] = "replacement"
    receiver.accept(v1.Publication.model_validate(replacement))
    reply = receiver.retire(
        retirement.model_copy(update={"retirement_id": "replacement-retirement"})
    )
    assert reply.status == "retired"
    assert receiver.active == ("workspace-a", "replacement", 1)


def test_immutable_generation_and_activation_identity_reject_conflicts():
    _, receiver, publication, request = example()
    receiver.accept(publication)
    conflict = deepcopy(publication.model_dump())
    conflict["definitions"][0]["prompt"] = "Different definition"
    with pytest.raises(ValueError, match="immutable publication conflict"):
        receiver.accept(v1.Publication.model_validate(conflict))
    receiver.activate(request)
    with pytest.raises(ValueError, match="identity payload conflict"):
        receiver.activate(
            request.model_copy(update={"expected_activation_revision": 2})
        )


def test_publication_lineage_and_partial_replacement_are_invalid():
    data = load_fixture("publication")
    data["definitions"][0]["publication_generation"] = 2
    with pytest.raises(ValidationError, match="belong"):
        v1.Publication.model_validate(data)
    retirement = load_fixture("retirement-request")
    retirement["replacement_generation"] = 2
    with pytest.raises(ValidationError, match="both"):
        v1.RetirementRequest.model_validate(retirement)
