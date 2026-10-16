"""Maia v1 input/provenance validation without executing a model."""

import json
from pathlib import Path

import pytest
from tempo_analysis_jobs_contracts.v1 import MaiaJob, MaiaResult, serialize_contract

from tempo_intelligence.identity import digest, source_descriptor
from tempo_intelligence.maia import build_maia_job, validate_maia_result
from tempo_intelligence.models import RequestInput, Snapshot


def fixture(name):
    return json.loads((Path(__file__).parent.parent / "fixtures" / name).read_text())


def valid_pair():
    return MaiaJob.model_validate(fixture("maia-job-v1.json")), MaiaResult.model_validate(fixture("maia-result-v1.json"))


def test_versioned_maia_fixture_is_bound_to_complete_position_input():
    request = RequestInput.model_validate(fixture("request-v1.json"))
    descriptor = source_descriptor(request.snapshot, request.sources[2])
    cache_id = digest({"workspace": request.snapshot.workspace_id, "descriptor": {key: value for key, value in descriptor.items() if key != "legal_universe_digest"}})
    job = build_maia_job(descriptor, request.snapshot.workspace_id, cache_id, 1, 1)
    assert job == valid_pair()[0]
    assert validate_maia_result(job, valid_pair()[1]) is None


def test_generated_maia_job_budget_matches_supported_one_gib_worker_envelope():
    request = RequestInput.model_validate(fixture("request-v1.json"))
    descriptor = source_descriptor(request.snapshot, request.sources[2])
    job = build_maia_job(descriptor, "fixture", "budget-cache", 1, 1)
    assert job.constraints.max_memory_mb == 1024
    assert job.constraints.max_wall_time_ms == 5000


def test_maia_artifact_replacement_changes_reproducible_input_identity():
    request = RequestInput.model_validate(fixture("request-v1.json"))
    descriptor = source_descriptor(request.snapshot, request.sources[2])
    replacement = {**descriptor, "model_version": "fixture-2", "artifact_sha256": "1" * 64}
    assert digest(descriptor) != digest(replacement)
    first = build_maia_job(descriptor, "fixture", "cache-a", 1, 1)
    second = build_maia_job(replacement, "fixture", "cache-b", 1, 1)
    assert first.job_id != second.job_id and first.input_digest != second.input_digest


def test_execution_retry_preserves_job_semantics_and_advances_generation():
    request = RequestInput.model_validate(fixture("request-v1.json"))
    descriptor = source_descriptor(request.snapshot, request.sources[2])
    first = build_maia_job(descriptor, "fixture", "same-cache", 1, 1)
    retry = build_maia_job(descriptor, "fixture", "same-cache", 1, 2)
    assert first.model_dump(exclude={"execution_generation"}) == retry.model_dump(exclude={"execution_generation"})
    assert retry.execution_generation == 2


@pytest.mark.parametrize("change", [
    {"workspace_id": "other-workspace"}, {"job_id": "other-job"},
    {"request_revision": 2}, {"execution_generation": 2},
])
def test_maia_result_rejects_wrong_workspace_job_or_generation(change):
    job, result = valid_pair()
    with pytest.raises(ValueError, match="maia_identity_mismatch"):
        validate_maia_result(job, result.model_copy(update=change))


def test_maia_result_rejects_wrong_input_digest():
    job, result = valid_pair()
    provenance = result.provenance.model_copy(update={"input_digest": "sha256:wrong-input"})
    with pytest.raises(ValueError, match="maia_identity_mismatch"):
        validate_maia_result(job, result.model_copy(update={"provenance": provenance}))


@pytest.mark.parametrize("change", [{"model_id": "wrong-model"}, {"model_version": "wrong-version"}])
def test_maia_result_rejects_wrong_model_identity(change):
    job, result = valid_pair()
    with pytest.raises(ValueError, match="maia_model_mismatch"):
        validate_maia_result(job, result.model_copy(update=change))


def test_maia_result_preserves_explicit_zero_for_every_unselected_legal_move():
    job, result = valid_pair()
    validate_maia_result(job, result)
    assert len(result.predictions) == len(job.legal_moves) == 20
    assert sum(item.probability == 0.0 for item in result.predictions) == 19
    assert '"probability":0.0' in serialize_contract(result)


def test_maia_result_rejects_missing_or_illegal_prediction():
    job, result = valid_pair()
    missing = result.model_copy(update={"predictions": [item for item in result.predictions if item.move != "a2a3"]})
    with pytest.raises(ValueError, match="maia_prediction_moves_mismatch"):
        validate_maia_result(job, missing)
    illegal = result.predictions[0].model_copy(update={"move": "e2e5"})
    with pytest.raises(ValueError, match="maia_prediction_moves_mismatch"):
        validate_maia_result(job, result.model_copy(update={"predictions": [illegal, *result.predictions[1:]]}))


def test_maia_result_rejects_incomplete_requested_legal_universe():
    job, result = valid_pair()
    with pytest.raises(ValueError, match="maia_legal_universe_mismatch"):
        validate_maia_result(job.model_copy(update={"legal_moves": ["e2e4", "d2d4"]}), result)


@pytest.mark.parametrize("probability", [-0.1, float("inf"), float("nan"), 0.5])
def test_maia_result_revalidates_finite_nonnegative_normalized_mass(probability):
    job, result = valid_pair()
    predictions = [item.model_copy(update={"probability": probability}) if item.move == "e2e4" else item for item in result.predictions]
    with pytest.raises(ValueError, match="invalid_maia_payload"):
        validate_maia_result(job, result.model_copy(update={"predictions": predictions}))


def test_maia_result_rejects_duplicate_moves_even_if_mass_still_one():
    job, result = valid_pair()
    duplicate = result.predictions[0].model_copy(update={"move": result.predictions[1].move})
    with pytest.raises(ValueError, match="invalid_maia_payload"):
        validate_maia_result(job, result.model_copy(update={"predictions": [duplicate, *result.predictions[1:]]}))


def test_terminal_position_does_not_create_a_maia_distribution():
    request = RequestInput.model_validate(fixture("request-v1.json"))
    terminal = Snapshot.model_validate(fixture("terminal-position-v1.json"))
    descriptor = source_descriptor(terminal, request.sources[2])
    with pytest.raises(ValueError, match="terminal_position"):
        build_maia_job(descriptor, "fixture", "terminal-cache", 1, 1)


@pytest.mark.parametrize("name", ["insufficient-material-position-v1.json", "seventyfive-move-position-v1.json"])
def test_terminal_draw_with_legal_moves_cannot_create_maia_job(name):
    request = RequestInput.model_validate(fixture("request-v1.json"))
    snapshot = Snapshot.model_validate(fixture(name))
    descriptor = source_descriptor(snapshot, request.sources[2])
    assert descriptor["legal_moves"]
    with pytest.raises(ValueError, match="terminal_position"):
        build_maia_job(descriptor, "fixture", "terminal-cache", 1, 1)


def test_intrinsically_dead_board_cannot_bypass_maia_guard_by_omitting_terminal_flag():
    request = RequestInput.model_validate(fixture("request-v1.json"))
    snapshot = Snapshot.model_validate(fixture("insufficient-material-position-v1.json"))
    descriptor = source_descriptor(snapshot, request.sources[2])
    descriptor.pop("terminal_reason")
    with pytest.raises(ValueError, match="terminal_position"):
        build_maia_job(descriptor, "fixture", "terminal-cache", 1, 1)
