"""Consumer-maintained decisions; independent of producer construction."""

import json
from tempo_intelligence_contracts import v1


def consume(record):
    model = getattr(v1, record["model"])
    value = v1.validate_contract(model, json.dumps(record["payload"]))
    assert value.workspace_id == "workspace-a"
    if record["model"] == "IntelligenceAvailability":
        assert (value.publication is not None) == (value.status == "available")
        return (
            "advisory_only"
            if value.publication is not None
            else "deterministic_fallback"
        )
    if value.advisory.kind == "probabilities":
        assert value.advisory.moves[0].probability == 0
        assert value.advisory.moves[1].probability is None
        return "preserve_unknown_probability"
    return "advisory_only"
