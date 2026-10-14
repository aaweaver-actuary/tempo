"""Consumer-maintained decisions; independent of producer construction."""

import json
from tempo_analysis_jobs_contracts import v1


def consume(record):
    model = getattr(v1, record["model"])
    value = v1.validate_contract(model, json.dumps(record["payload"]))
    assert value.workspace_id == "workspace-a"
    if record["model"] == "CancellationAcknowledgement":
        return (
            "computation_stopped"
            if value.status == "terminated"
            else "termination_unconfirmed"
        )
    if record["model"] in ("StockfishJob", "MaiaJob"):
        assert value.constraints.max_wall_time_ms == 5000
        return "bounded_job"
    if record["model"] == "StockfishResult":
        assert value.variations[0].score_perspective == "side_to_move"
        return "engine_result"
    if record["model"] == "MaiaResult":
        assert abs(sum(item.probability for item in value.predictions) - 1) < 1e-9
        return "model_result"
    if record["model"] == "JobStatus":
        assert not value.retryable or value.status == "failed"
    return "job_control"
