import pytest
from pydantic import ValidationError
from tempo_analysis_jobs_contracts import v1
from .conftest import load_fixture, load_reference


@pytest.mark.parametrize(
    "worker,job_type,result_type",
    [
        ("stockfish", v1.StockfishJob, v1.StockfishResult),
        ("maia", v1.MaiaJob, v1.MaiaResult),
    ],
)
def test_bounded_worker_result_matches_request_and_provenance(
    worker, job_type, result_type
):
    reference = load_reference()
    job = job_type.model_validate(load_fixture(worker + "-job"))
    result = result_type.model_validate(load_fixture(worker + "-result"))
    assert reference.accept_result(job, result, current_generation=1) == result
    for changes in [
        {"execution_generation": 2},
        {"workspace_id": "other"},
        {"request_revision": 2},
    ]:
        with pytest.raises(ValueError, match="stale result"):
            reference.accept_result(
                job, result.model_copy(update=changes), current_generation=1
            )
    with pytest.raises(ValueError, match="stale result"):
        reference.accept_result(job, result, current_generation=2)


@pytest.mark.parametrize(
    "worker,job_type,result_type",
    [
        ("stockfish", v1.StockfishJob, v1.StockfishResult),
        ("maia", v1.MaiaJob, v1.MaiaResult),
    ],
)
def test_cancellation_requested_is_not_computation_termination(
    worker, job_type, result_type
):
    reference = load_reference()
    request = v1.CancellationRequest.model_validate(load_fixture("cancel-request"))
    requested = v1.CancellationAcknowledgement.model_validate(
        load_fixture("cancel-requested")
    )
    terminated = v1.CancellationAcknowledgement.model_validate(
        load_fixture("cancel-terminated")
    )
    assert not reference.computation_terminated(request, requested)
    assert reference.computation_terminated(request, terminated)
    job = job_type.model_validate(load_fixture(worker + "-job"))
    result = result_type.model_validate(load_fixture(worker + "-result"))
    assert (
        reference.accept_result(
            job, result, current_generation=1, cancellation_status="requested"
        )
        == result
    )
    with pytest.raises(ValueError, match="stale result"):
        reference.accept_result(
            job, result, current_generation=1, cancellation_status="terminated"
        )
    with pytest.raises(ValueError, match="identity mismatch"):
        reference.computation_terminated(
            request, terminated.model_copy(update={"execution_generation": 2})
        )


def test_stockfish_and_maia_cannot_exchange_results():
    job = v1.StockfishJob.model_validate(load_fixture("stockfish-job"))
    result = v1.MaiaResult.model_validate(load_fixture("maia-result"))
    with pytest.raises(ValueError, match="wrong worker"):
        load_reference().accept_result(job, result, current_generation=1)


def test_result_model_and_engine_versions_are_fenced():
    reference = load_reference()
    for worker, job_type, result_type, field in [
        ("stockfish", v1.StockfishJob, v1.StockfishResult, "engine_version"),
        ("maia", v1.MaiaJob, v1.MaiaResult, "model_version"),
    ]:
        job = job_type.model_validate(load_fixture(worker + "-job"))
        result = result_type.model_validate(load_fixture(worker + "-result"))
        with pytest.raises(ValueError, match="outside"):
            reference.accept_result(
                job,
                result.model_copy(update={field: "wrong-version"}),
                current_generation=1,
            )


def test_failed_execution_requires_explicit_retryability_and_reason():
    data = load_fixture("status-running")
    data["retryable"] = True
    with pytest.raises(ValidationError):
        v1.JobStatus.model_validate(data)
    data = load_fixture("maia-result")
    data["predictions"][0]["probability"] = 0.9
    with pytest.raises(ValidationError, match="sum to one"):
        v1.MaiaResult.model_validate(data)


@pytest.mark.parametrize(
    "worker,job_type", [("stockfish", v1.StockfishJob), ("maia", v1.MaiaJob)]
)
def test_same_job_request_replay_and_new_execution_generation_preserve_input(
    worker, job_type
):
    requests = {}
    receive = load_reference().receive_job
    job = job_type.model_validate(load_fixture(worker + "-job"))
    key = receive(requests, job)
    assert receive(requests, job) == key
    assert receive(requests, job.model_copy(update={"execution_generation": 2})) == key
    assert len(requests) == 1
    with pytest.raises(ValueError, match="payload conflict"):
        receive(requests, job.model_copy(update={"input_digest": "different-input"}))


@pytest.mark.parametrize(
    "worker,result_type", [("stockfish", v1.StockfishResult), ("maia", v1.MaiaResult)]
)
@pytest.mark.parametrize(
    "finished_at,accepted",
    [
        ("2026-10-14T12:00:00.000000Z", False),
        ("2026-10-14T12:00:00.000001Z", True),
        ("2026-10-14T12:00:00.000002Z", True),
        ("2026-10-14T14:00:00.000001+02:00", True),
    ],
    ids=[
        "one_microsecond_earlier",
        "same_instant",
        "one_microsecond_later",
        "offset_equivalent_instant",
    ],
)
def test_execution_microsecond_chronology_preserves_original_wire_spelling(
    worker, result_type, finished_at, accepted
):
    import json

    data = load_fixture(worker + "-result")
    data["provenance"]["started_at"] = "2026-10-14T12:00:00.000001Z"
    data["provenance"]["finished_at"] = finished_at
    if not accepted:
        with pytest.raises(ValidationError, match="execution finishes"):
            result_type.model_validate(data)
    else:
        assert (
            json.loads(v1.serialize_contract(result_type.model_validate(data))) == data
        )
