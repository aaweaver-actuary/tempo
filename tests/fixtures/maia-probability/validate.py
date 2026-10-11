"""Validate Node source results through Python's authoritative source adapter."""
import json
import sys

from app.maia_probability_contracts import (
    MaiaInferenceResult, MaiaRatingContext, build_maia_evidence, evidence_checksum,
    load_maia_model_descriptor, maia_cache_key,
)
from app.opening_move_probability_contracts import PositionMoveUniverse, serialize_contract

results = []
for payload in json.load(sys.stdin):
    try:
        result = MaiaInferenceResult.model_validate(payload)
        model = load_maia_model_descriptor()
        position = PositionMoveUniverse.model_validate(payload["position"])
        ratings = MaiaRatingContext.model_validate(payload["ratings"])
        snapshot = build_maia_evidence(result, position=position, ratings=ratings, model=model,
            expected_side_to_move="white" if position.fen_key.split()[1] == "w" else "black",
            source_generation="shared-offline-fixture", calculated_at="2026-10-11T07:00:00Z")
        results.append({"valid": True, "evidence": json.loads(serialize_contract(snapshot)),
                        "cache_key": maia_cache_key(position, model, ratings),
                        "checksum": evidence_checksum(snapshot)})
    except ValueError:
        results.append({"valid": False})
print(json.dumps(results, allow_nan=False))
