"""Consumer-maintained decisions; independent of producer construction."""

import json
from tempo_content_publication_contracts import v1


def consume(record):
    model = getattr(v1, record["model"])
    value = v1.validate_contract(model, json.dumps(record["payload"]))
    assert value.workspace_id == "workspace-a"
    if record["model"] == "Publication":
        assert (
            value.definitions[0].publication_generation == value.publication_generation
        )
        return "offer_to_learning"
    if record["model"] == "PublicationAcceptance":
        return (
            "await_activation"
            if value.status == "accepted_pending"
            else "publication_not_accepted"
        )
    if record["model"] == "ActivationAcknowledgement":
        return (
            "observe_activation" if value.status == "activated" else "do_not_activate"
        )
    if record["model"] == "RetirementAcknowledgement":
        assert value.stale_attempts_fenced == (value.status == "retired")
        return (
            "observe_retirement"
            if value.stale_attempts_fenced
            else "await_or_reject_retirement"
        )
    return "validated_publication_input"
