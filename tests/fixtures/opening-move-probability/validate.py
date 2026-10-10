"""Shared fixture boundary probe; no provider, app startup, or database imports."""
import json
import sys

from app.opening_move_probability_contracts import (
    FusedMoveDistribution, FusionRequest, OpeningMoveEvidence,
    OpeningMoveEvidenceBundle, PositionMoveUniverse, serialize_contract,
)

SCHEMAS = {"source": OpeningMoveEvidence, "bundle": OpeningMoveEvidenceBundle,
           "request": FusionRequest, "fused": FusedMoveDistribution, "position": PositionMoveUniverse}

results = []
for case in json.load(sys.stdin):
    try:
        value = SCHEMAS[case["schema"]].model_validate(case["payload"])
        serialized = serialize_contract(value)
        results.append({"valid": True, "normalized": json.loads(serialized), "serialized": serialized})
    except ValueError:
        results.append({"valid": False})
print(json.dumps(results, allow_nan=False))
