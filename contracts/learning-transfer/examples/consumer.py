"""Consume without treating wire evidence as a storage or activation proof."""
import json
from tempo_learning_transfer_contracts import v1


def consume(record):
    value = v1.validate_contract(getattr(v1, record["model"]), json.dumps(record["payload"]))
    assert isinstance(value, v1.SourceManifest)
    assert value.scope == "representative"
    plan = v1.plan_transfer(value)
    return "retain_blocked_identity" if plan.status == "BLOCK" else "stage_representative_only"
