"""Independent installed-contract consumer; fixture probabilities are not model evidence."""
import importlib.util
from pathlib import Path

import pytest
from tempo_analysis_jobs_contracts import v1

spec = importlib.util.spec_from_file_location("maia_independent_reference", Path(__file__).with_name("reference_consumer.py"))
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)


def sample_pair():
    job = v1.MaiaJob.model_validate({
        "contract_version": 1, "worker": "maia", "workspace_id": "workspace-fixture", "job_id": "job-fixture",
        "request_revision": 1, "execution_generation": 1, "input_digest": "opaque-fixture-digest",
        "constraints": {"max_wall_time_ms": 30000, "max_memory_mb": 1024},
        "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "rating": 1500, "model_id": "maia3-onnx", "model_version": "contract-fixture-only", "legal_moves": ["e2e4", "d2d4"],
    })
    result = v1.MaiaResult.model_validate({
        "contract_version": 1, "worker": "maia", "workspace_id": job.workspace_id, "job_id": job.job_id,
        "request_revision": 1, "execution_generation": 1, "model_id": job.model_id, "model_version": job.model_version,
        "provenance": {"input_digest": job.input_digest, "runtime_name": "fixture", "runtime_version": "1", "implementation_version": "fixture",
                       "started_at": "2026-10-16T12:00:00Z", "finished_at": "2026-10-16T12:00:01Z"},
        "predictions": [{"move": "e2e4", "probability": 0.6}, {"move": "d2d4", "probability": 0.4}],
    })
    return job, result


class RequesterLedger:
    """Test-only requesting-domain state; no worker persistence or production integration."""
    def __init__(self):
        self.requests = {}
        self.generations = {}
        self.accepted = {}

    def submit(self, job):
        key = reference.receive_job(self.requests, job)
        self.generations[key] = job.execution_generation
        return key

    def accept(self, job, result, cancellation_status=None):
        key = (job.workspace_id, job.job_id, job.request_revision)
        reference.accept_result(job, result, current_generation=self.generations[key], cancellation_status=cancellation_status)
        if key in self.accepted:
            previous = self.accepted[key]
            if previous.predictions != result.predictions or previous.model_id != result.model_id or previous.model_version != result.model_version:
                raise ValueError("conflicting accepted model effect")
            return False
        self.accepted[key] = result
        return True


def test_contract_distribution_is_installed_and_reference_consumes_fixture():
    assert "site-packages" in str(Path(v1.__file__).resolve())
    job, result = sample_pair()
    assert reference.accept_result(job, result, current_generation=1) == result


@pytest.mark.parametrize("field,value", [("workspace_id", "wrong"), ("job_id", "wrong"), ("request_revision", 2), ("execution_generation", 2), ("model_id", "wrong"), ("model_version", "wrong")])
def test_independent_consumer_rejects_wrong_result_identity(field, value):
    job, result = sample_pair()
    payload = result.model_dump()
    payload[field] = value
    with pytest.raises(ValueError):
        reference.accept_result(job, v1.MaiaResult.model_validate(payload), current_generation=1)


@pytest.mark.parametrize("mutation", ["digest", "missing", "unexpected", "duplicate", "mass", "nan", "extra"])
def test_schema_and_consumer_reject_adversarial_results(mutation):
    job, result = sample_pair()
    payload = result.model_dump()
    if mutation == "digest": payload["provenance"]["input_digest"] = "wrong"
    elif mutation == "missing": payload["predictions"] = [{"move": "e2e4", "probability": 1.0}]
    elif mutation == "unexpected": payload["predictions"][1]["move"] = "a2a3"
    elif mutation == "duplicate": payload["predictions"][1]["move"] = "e2e4"
    elif mutation == "mass": payload["predictions"][1]["probability"] = 0.2
    elif mutation == "nan": payload["predictions"][1]["probability"] = float("nan")
    else: payload["self_rating"] = 1500
    with pytest.raises(ValueError):
        parsed = v1.MaiaResult.model_validate(payload)
        reference.accept_result(job, parsed, current_generation=1)


def test_response_loss_replay_and_restart_have_one_accepted_effect():
    job, result = sample_pair()
    ledger = RequesterLedger()
    ledger.submit(job)
    assert ledger.accept(job, result)
    assert not ledger.accept(job, result)
    restarted_job = job.model_copy(update={"execution_generation": 2})
    ledger.submit(restarted_job)
    with pytest.raises(ValueError, match="stale"):
        ledger.accept(job, result)
    new_result = result.model_copy(update={"execution_generation": 2})
    assert not ledger.accept(restarted_job, new_result)
    assert len(ledger.accepted) == 1


def test_immutable_request_conflict_includes_constraints_and_move_order():
    job, _ = sample_pair()
    for replacement in [{"rating": 1600}, {"legal_moves": list(reversed(job.legal_moves))}, {"constraints": {"max_wall_time_ms": 60000, "max_memory_mb": 1024}}]:
        ledger = RequesterLedger()
        ledger.submit(job)
        with pytest.raises(ValueError, match="conflict"):
            ledger.submit(v1.MaiaJob.model_validate(job.model_dump() | replacement))


def test_confirmed_cancellation_prevents_result_acceptance():
    job, result = sample_pair()
    ledger = RequesterLedger()
    ledger.submit(job)
    with pytest.raises(ValueError, match="stale"):
        ledger.accept(job, result, cancellation_status="terminated")
    assert ledger.accept(job, result, cancellation_status="requested")


@pytest.mark.parametrize("status", ["requested", "terminated", "already_completed", "stale_generation"])
def test_cancellation_acknowledgements_bind_exact_identity(status):
    job, _ = sample_pair()
    request = v1.CancellationRequest.model_validate({key: value for key, value in job.model_dump().items() if key in {"contract_version", "workspace_id", "job_id", "request_revision", "execution_generation"}} | {"cancellation_id": "cancel-fixture"})
    ack = v1.CancellationAcknowledgement.model_validate(request.model_dump() | {"status": status, "acknowledged_at": "2026-10-16T12:00:01Z"})
    assert reference.computation_terminated(request, ack) == (status == "terminated")
    with pytest.raises(ValueError, match="identity"):
        reference.computation_terminated(request, ack.model_copy(update={"execution_generation": 2}))
