"""Consume actual installed-worker exchanges with the installed Python contract."""
import argparse
import importlib.util
import json
from pathlib import Path

from tempo_analysis_jobs_contracts import v1

parser = argparse.ArgumentParser()
parser.add_argument("--exchange", type=Path, required=True)
arguments = parser.parse_args()
assert "site-packages" in str(Path(v1.__file__).resolve()), "contract must be installed"
spec = importlib.util.spec_from_file_location("maia_observed_reference", Path(__file__).with_name("reference_consumer.py"))
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)
transcript = json.loads(arguments.exchange.read_text())
assert transcript.get("exchanges"), "zero actual exchanges"
identities = []
ratings = set()
sides = set()
for index, exchange in enumerate(transcript["exchanges"]):
    job = v1.MaiaJob.model_validate(exchange["job"])
    result = v1.MaiaResult.model_validate(exchange["result"])
    reference.accept_result(job, result, current_generation=job.execution_generation)
    assert result.provenance.runtime_name == "onnxruntime-web/wasm"
    assert result.provenance.runtime_version == "1.23.0"
    assert job.model_id == "maia3-onnx"
    assert job.rating in range(1100, 1901, 100)
    ratings.add(job.rating)
    sides.add(job.fen.split()[1])
    identities.append(f"actual installed exchange {index + 1}: {job.job_id}")
assert {1100, 1900} <= ratings, "actual smoke must cover rating endpoints"
assert sides == {"w", "b"}, "actual smoke must cover both board orientations"
assert transcript.get("stale_results"), "actual restarted-generation stale-result proof missing"
for index, exchange in enumerate(transcript["stale_results"]):
    job = v1.MaiaJob.model_validate(exchange["job"])
    result = v1.MaiaResult.model_validate(exchange["result"])
    try:
        reference.accept_result(job, result, current_generation=exchange["current_generation"])
    except ValueError:
        identities.append(f"actual stale result {index + 1}: rejected after worker restart")
    else:
        raise AssertionError("stale execution result accepted after worker restart")
terminated = set()
model_names = {"MaiaResult", "JobStatus", "CancellationAcknowledgement"}
for index, message in enumerate(transcript.get("messages", [])):
    assert set(message) == {"model", "payload"}
    assert message["model"] in model_names
    parsed = getattr(v1, message["model"]).model_validate(message["payload"])
    key = (parsed.workspace_id, parsed.job_id, parsed.request_revision, parsed.execution_generation)
    if isinstance(parsed, v1.CancellationAcknowledgement) and parsed.status == "terminated":
        terminated.add(key)
    if isinstance(parsed, v1.MaiaResult):
        assert key not in terminated, "result published after confirmed termination"
    identities.append(f"actual strict message {index + 1}: {message['model']}")
assert terminated, "actual in-flight termination proof missing"
print(json.dumps({"success": True, "passed": len(identities), "failed": 0, "executedIdentities": identities}))
