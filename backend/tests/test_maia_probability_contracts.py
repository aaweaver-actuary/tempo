"""Offline source-adapter regressions, independent of the unmerged inventory."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from app.opening_move_probability_contracts import move_universe, serialize_contract, unknown_distribution
from app.maia_probability_contracts import (
    MaiaInferenceResult, MaiaModelDescriptor, MaiaRatingContext, build_maia_evidence,
    evidence_checksum, load_maia_model_descriptor, maia_cache_key,
)

POSITION = move_universe("4k3/8/8/8/8/8/8/4K3 b - - 0 1")
RATINGS = MaiaRatingContext(side_to_move_rating=1500, other_player_rating=1600)
MODEL = load_maia_model_descriptor()
CAPTURED_AT = "2026-10-11T07:00:00Z"


def valid_result():
    return {"contract_version": 1, "position": POSITION.model_dump(),
            "side_to_move": "black", "ratings": RATINGS.model_dump(),
            "model": MODEL.model_dump(),
            "moves": [{"move_uci": move, "probability": probability}
                      for move, probability in zip(POSITION.legal_moves, (.5, .2, .2, .1, 0), strict=True)]}


def evidence(payload=None, **overrides):
    arguments = dict(position=POSITION, ratings=RATINGS, model=MODEL, expected_side_to_move="black",
                     source_generation="fixture-generation-1", calculated_at=CAPTURED_AT)
    return build_maia_evidence(MaiaInferenceResult.model_validate(payload or valid_result()),
                               **{**arguments, **overrides})


def test_maia_cache_identity_is_shared_across_repertoires_and_counter_variations():
    first_repertoire = move_universe("4k3/8/8/8/8/8/8/4K3 b - - 0 1")
    second_repertoire = move_universe("4k3/8/8/8/8/8/8/4K3 b - - 99 77")
    assert maia_cache_key(first_repertoire, MODEL, RATINGS) == maia_cache_key(second_repertoire, MODEL, RATINGS)
    assert evidence().distribution.position == first_repertoire == second_repertoire


def test_maia_cache_identity_distinguishes_ordered_ratings_model_schema_and_runtime():
    original = maia_cache_key(POSITION, MODEL, RATINGS)
    for ratings in [MaiaRatingContext(side_to_move_rating=1600, other_player_rating=1500),
                    MaiaRatingContext(side_to_move_rating=1500, other_player_rating=1700)]:
        assert maia_cache_key(POSITION, MODEL, ratings) != original
    for field, value in [("model_sha256", "0" * 64), ("move_index_sha256", "0" * 64),
                         ("reverse_index_sha256", "0" * 64), ("inference_schema", "maia-legal-softmax-v2"),
                         ("runtime_version", "future-runtime"), ("chess_library_version", "future-chess")]:
        model = MaiaModelDescriptor.model_validate({**MODEL.model_dump(), field: value})
        assert maia_cache_key(POSITION, model, RATINGS) != original


@pytest.mark.parametrize("unsupported", [1000, 2000, 1550, True, "1500", 1500.0, None])
def test_maia_rating_context_rejects_unsupported_levels_without_rounding(unsupported):
    with pytest.raises(ValueError):
        MaiaRatingContext(side_to_move_rating=unsupported, other_player_rating=1500)
    with pytest.raises(ValueError):
        MaiaRatingContext(side_to_move_rating=1500, other_player_rating=unsupported)


def test_maia_rating_context_rejects_invented_time_control():
    with pytest.raises(ValueError):
        MaiaRatingContext.model_validate({**RATINGS.model_dump(), "speed": "rapid"})


def test_maia_evidence_preserves_all_legal_replies_and_explicit_zero_vs_unknown():
    snapshot = evidence()
    assert len(snapshot.distribution.moves) == len(POSITION.legal_moves)
    assert snapshot.distribution.moves[-1].probability == 0
    assert snapshot.distribution.unknown_mass == 0
    unknown = unknown_distribution(POSITION)
    assert unknown.moves[-1].probability is None and unknown.unknown_mass == 1
    assert snapshot.raw_evidence.basis == "probability"
    assert snapshot.normalization.parameters["denominator"] == "all_legal_moves"
    assert snapshot.cohort == RATINGS.model_dump()
    assert snapshot.freshness.state == "unknown" and snapshot.freshness.valid_until is None


@pytest.mark.parametrize("probability", [-.1, float("nan"), float("inf"), True, "0.5", None])
def test_maia_source_rejects_invalid_probabilities(probability):
    payload = valid_result()
    payload["moves"][0]["probability"] = probability
    with pytest.raises(ValueError):
        evidence(payload)


@pytest.mark.parametrize("defect", ["illegal", "duplicate", "missing", "missing_mass", "excess_mass", "wrong_side"])
def test_maia_source_rejects_illegal_incomplete_and_wrong_orientation_results(defect):
    payload = valid_result()
    if defect == "illegal":
        payload["moves"][0]["move_uci"] = "e8e6"
    elif defect == "duplicate":
        payload["moves"][0] = deepcopy(payload["moves"][1])
    elif defect == "missing":
        payload["moves"].pop()
    elif defect == "missing_mass":
        payload["moves"][0]["probability"] -= .01
    elif defect == "excess_mass":
        payload["moves"][0]["probability"] += .01
    else:
        payload["side_to_move"] = "white"
    with pytest.raises(ValueError):
        evidence(payload)


def test_maia_source_rejects_stale_model_rating_and_position_provenance():
    payload = valid_result()
    payload["model"]["model_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="provenance"):
        evidence(payload)
    with pytest.raises(ValueError, match="provenance"):
        evidence(ratings=MaiaRatingContext(side_to_move_rating=1400, other_player_rating=1600))
    with pytest.raises(ValueError, match="provenance"):
        evidence(position=move_universe("4k3/8/8/8/8/8/8/4K3 w - - 0 1"))
    with pytest.raises(ValueError, match="opponent turn"):
        evidence(expected_side_to_move="white")


def test_maia_evidence_serialization_checksum_and_provenance_are_deterministic():
    payload = valid_result()
    payload["moves"].reverse()
    assert serialize_contract(evidence(payload)) == serialize_contract(evidence())
    assert evidence_checksum(evidence(payload)) == evidence_checksum(evidence())
    snapshot = evidence()
    assert "upstream_checkpoint_unrecorded" in snapshot.provenance[0].limitations
    assert "time_control_not_conditioned" in snapshot.quality.flags
    assert snapshot.provenance[0].input_fingerprint == maia_cache_key(POSITION, MODEL, RATINGS)
    assert evidence_checksum(evidence(source_generation="fixture-generation-2")) != evidence_checksum(snapshot)


def test_maia_manifest_and_source_contract_import_do_not_load_model_or_database():
    # A regular producer/consumer fixture is shared with the Node adapter.
    fixtures = json.loads((Path(__file__).parents[2] / "tests/fixtures/maia-probability/examples.json").read_text())
    for fixture in fixtures["cases"]:
        assert move_universe(fixture["fen"]).model_dump(mode="json") == fixture["position"]
    assert MODEL.model_sha256 == "405bf76c15727dad8728b352c06a8f3c1b80fb2760e8d666b32485c63d75b856"
    subprocess.run([sys.executable, "-c", "import sys; from app.maia_probability_contracts import load_maia_model_descriptor; "
                    "load_maia_model_descriptor(); assert 'app.database' not in sys.modules; "
                    "assert 'app.tasks' not in sys.modules; assert 'onnxruntime' not in sys.modules"],
                   check=True, timeout=15)
