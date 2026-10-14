"""Consumer-maintained decisions; independent of producer construction."""

import json
from tempo_game_evidence_contracts import v1


def consume(record):
    model = getattr(v1, record["model"])
    value = v1.validate_contract(model, json.dumps(record["payload"]))
    assert value.workspace_id == "workspace-a"
    if record["model"] == "EvidenceDelivery":
        assert value.encounters[0].observed_move == "d2d4"
        assert value.comparisons[0].status == "missed"
        assert value.obligations[0].source_evidence_id == value.evidence_id
        return "submit_obligations_without_queue_write"
    if record["model"] == "EvidenceAcknowledgement":
        assert (value.learning_revision is not None) == (value.status == "completed")
        return (
            "acknowledge_completion"
            if value.status == "completed"
            else "keep_delivery_pending_or_failed"
        )
    assert value.provenance.provider_game_id == "external-a"
    return "observed_game"
