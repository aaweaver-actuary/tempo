"""Reference Learning receiver and Content acknowledgement consumer only."""

from copy import deepcopy
from tempo_content_publication_contracts import v1


class ReferenceActivation:
    def __init__(self):
        self.publications = {}
        self.latest_generation = {}
        self.activation_revision = 1
        self.active = None
        self.retired = set()
        self.activation_operations = {}
        self.retirement_operations = {}

    def accept(self, publication: v1.Publication):
        identity = (
            publication.workspace_id,
            publication.publication_id,
            publication.publication_generation,
        )
        payload = v1.serialize_contract(publication)
        if identity in self.publications and self.publications[identity] != payload:
            raise ValueError("immutable publication conflict")
        latest_key = identity[:2]
        if publication.publication_generation < self.latest_generation.get(
            latest_key, 0
        ):
            raise ValueError("stale publication generation")
        self.publications[identity] = payload
        self.latest_generation[latest_key] = publication.publication_generation
        return "accepted_pending"

    def activate(self, request: v1.ActivationRequest):
        operation = (request.workspace_id, request.activation_id)
        encoded = v1.serialize_contract(request)
        if operation in self.activation_operations:
            previous_payload, previous_reply = self.activation_operations[operation]
            if previous_payload != encoded:
                raise ValueError("activation identity payload conflict")
            return deepcopy(previous_reply)
        identity = (
            request.workspace_id,
            request.publication_id,
            request.publication_generation,
        )
        if identity not in self.publications:
            status = "rejected"
        elif (
            identity in self.retired
            or request.expected_activation_revision != self.activation_revision
            or request.publication_generation != self.latest_generation[identity[:2]]
        ):
            status = "stale_generation"
        else:
            self.active = identity
            self.activation_revision += 1
            status = "activated"
        reply = v1.ActivationAcknowledgement(
            contract_version=1,
            workspace_id=request.workspace_id,
            activation_id=request.activation_id,
            publication_id=request.publication_id,
            publication_generation=request.publication_generation,
            activation_revision=self.activation_revision,
            status=status,
            reason=None if status == "activated" else "not_current_or_not_accepted",
        )
        self.activation_operations[operation] = (encoded, reply)
        return deepcopy(reply)

    def retire(self, request: v1.RetirementRequest):
        operation = (request.workspace_id, request.retirement_id)
        encoded = v1.serialize_contract(request)
        if operation in self.retirement_operations:
            previous_payload, previous_reply = self.retirement_operations[operation]
            if previous_payload != encoded:
                raise ValueError("retirement identity payload conflict")
            return deepcopy(previous_reply)
        identity = (
            request.workspace_id,
            request.publication_id,
            request.publication_generation,
        )
        replacement = (
            request.workspace_id,
            request.replacement_publication_id,
            request.replacement_generation,
        )
        if (
            request.expected_activation_revision != self.activation_revision
            or self.active != identity
        ):
            status = "stale_generation"
        elif request.replacement_publication_id is not None and (
            replacement not in self.publications
            or replacement in self.retired
            or request.replacement_generation
            != self.latest_generation.get(replacement[:2])
        ):
            status = "rejected"
        else:
            self.retired.add(identity)
            self.active = (
                replacement if request.replacement_publication_id is not None else None
            )
            self.activation_revision += 1
            status = "retired"
        reply = v1.RetirementAcknowledgement(
            contract_version=1,
            workspace_id=request.workspace_id,
            retirement_id=request.retirement_id,
            publication_id=request.publication_id,
            publication_generation=request.publication_generation,
            activation_revision=self.activation_revision,
            status=status,
            stale_attempts_fenced=status == "retired",
            reason=None
            if status == "retired"
            else "not_current_or_replacement_not_accepted",
        )
        self.retirement_operations[operation] = (encoded, reply)
        return deepcopy(reply)


def observe_activation(
    request, acknowledgement, latest_generation, latest_activation_revision
):
    expected = (
        request.workspace_id,
        request.activation_id,
        request.publication_id,
        request.publication_generation,
    )
    received = (
        acknowledgement.workspace_id,
        acknowledgement.activation_id,
        acknowledgement.publication_id,
        acknowledgement.publication_generation,
    )
    return (
        acknowledgement.status == "activated"
        and expected == received
        and acknowledgement.publication_generation == latest_generation
        and acknowledgement.activation_revision >= latest_activation_revision
    )
