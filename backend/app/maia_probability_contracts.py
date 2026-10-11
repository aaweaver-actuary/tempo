"""Pure Maia source evidence and cache identity; no inference, storage or clocks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictInt, StrictStr, model_validator

from .opening_move_probability_contracts import (
    ContractModel, Identifier, MoveProbability, MoveProbabilityDistribution,
    OpeningMoveEvidence, PositionMoveUniverse, Timestamp, serialize_contract,
)

SUPPORTED_MAIA_RATINGS = tuple(range(1100, 2000, 100))
INFERENCE_SCHEMA = "maia-legal-softmax-v1"
Checksum = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]


class MaiaRatingContext(ContractModel):
    side_to_move_rating: StrictInt
    other_player_rating: StrictInt

    @model_validator(mode="after")
    def validate_levels(self):
        if any(rating not in SUPPORTED_MAIA_RATINGS
               for rating in (self.side_to_move_rating, self.other_player_rating)):
            raise ValueError("Unsupported Maia rating level; no rounding or interpolation policy is supplied")
        return self


class MaiaModelDescriptor(ContractModel):
    model_id: Literal["maia3-onnx"]
    model_sha256: Checksum
    move_index_sha256: Checksum
    reverse_index_sha256: Checksum
    inference_schema: Identifier
    runtime_version: Identifier
    chess_library_version: Identifier
    upstream_checkpoint: Identifier | None


def load_maia_model_descriptor() -> MaiaModelDescriptor:
    return MaiaModelDescriptor.model_validate_json(
        Path(__file__).with_name("maia_probability_manifest.json").read_text())


def _fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def maia_cache_key(position: PositionMoveUniverse, model: MaiaModelDescriptor,
                   ratings: MaiaRatingContext) -> str:
    """Versioned source identity excludes repertoire membership and caller clocks."""
    return "maia:v1:" + _fingerprint({
        "fen_key": position.fen_key, "model": model.model_dump(mode="json"),
        "ratings": ratings.model_dump(mode="json"),
    })


class MaiaInferenceResult(ContractModel):
    contract_version: Literal[1] = 1
    position: PositionMoveUniverse
    side_to_move: Literal["white", "black"]
    ratings: MaiaRatingContext
    model: MaiaModelDescriptor
    moves: tuple[MoveProbability, ...]

    @model_validator(mode="after")
    def validate_distribution(self):
        expected_side = "white" if self.position.fen_key.split()[1] == "w" else "black"
        if self.side_to_move != expected_side:
            raise ValueError("Maia result has incorrect side-to-move orientation")
        if any(move.probability is None for move in self.moves):
            raise ValueError("A complete Maia result cannot contain unknown probabilities")
        distribution = MoveProbabilityDistribution(position=self.position, moves=self.moves, unknown_mass=0)
        object.__setattr__(self, "moves", distribution.moves)
        return self


def build_maia_evidence(result: MaiaInferenceResult, *, position: PositionMoveUniverse,
                        ratings: MaiaRatingContext, model: MaiaModelDescriptor,
                        expected_side_to_move: Literal["white", "black"],
                        source_generation: str, calculated_at: Timestamp) -> OpeningMoveEvidence:
    """Accept only the requested source/position; never relabel a worker response."""
    result = MaiaInferenceResult.model_validate(result.model_dump())
    if (result.position != position or result.ratings != ratings or result.model != model):
        raise ValueError("Maia result provenance does not match the requested source inputs")
    if result.side_to_move != expected_side_to_move:
        raise ValueError("Maia request must select the expected opponent turn")
    if model.inference_schema != INFERENCE_SCHEMA:
        raise ValueError("Unsupported Maia inference schema")
    cache_key = maia_cache_key(position, model, ratings)
    limitations = ["time_control_not_conditioned", "current_position_only", "supported_rating_levels_1100_1900"]
    if model.upstream_checkpoint is None:
        limitations.append("upstream_checkpoint_unrecorded")
    references = [f"model-sha256:{model.model_sha256}",
                  f"move-index-sha256:{model.move_index_sha256}",
                  f"reverse-index-sha256:{model.reverse_index_sha256}",
                  f"onnxruntime-web:{model.runtime_version}",
                  f"chess.js:{model.chess_library_version}"]
    if model.upstream_checkpoint is not None:
        references.append(model.upstream_checkpoint)
    return OpeningMoveEvidence.model_validate({
        "evidence_id": "maia:" + _fingerprint({"cache_key": cache_key, "generation": source_generation}),
        "source_id": "maia", "source_generation": source_generation,
        "source_version": model.model_sha256, "cohort": ratings.model_dump(),
        "raw_evidence": {"kind": "scores", "basis": "probability", "moves": [
            {"move_uci": move.move_uci, "value": move.probability} for move in result.moves]},
        "distribution": {"position": position.model_dump(), "moves": [
            move.model_dump() for move in result.moves], "unknown_mass": 0},
        "normalization": {"id": "maia-legal-softmax", "version": "1", "parameters": {
            "denominator": "all_legal_moves", "temperature": 1,
            "zero_policy": "explicit_float64_softmax_underflow", "inference_schema": model.inference_schema}},
        "source_timestamp": calculated_at, "captured_at": calculated_at,
        "freshness": {"as_of": calculated_at, "valid_until": None, "state": "unknown"},
        "quality": {"effective_sample_size": None, "flags": limitations, "uncertainty": None},
        "provenance": [{"record_id": "maia-model-inference", "input_fingerprint": cache_key,
                        "references": references, "limitations": limitations}],
    })


def evidence_checksum(evidence: OpeningMoveEvidence) -> str:
    return hashlib.sha256(serialize_contract(evidence).encode()).hexdigest()
