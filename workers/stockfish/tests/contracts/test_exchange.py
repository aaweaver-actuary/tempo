import asyncio
import importlib
import json
from pathlib import Path
import sys
import pytest
from tempo_analysis_jobs_contracts import v1
from tempo_stockfish_client import StockfishClient, WorkerSessionLost, stockfish_input_digest

ROOT = Path(__file__).resolve().parents[2]


def test_client_and_shared_contract_imports_are_independently_installed():
    for name in ("tempo_stockfish_client", "tempo_analysis_jobs_contracts"):
        path = Path(importlib.import_module(name).__file__)
        assert "site-packages" in path.parts
        assert ROOT not in path.parents


def test_digest_golden_vector_preserves_nulls_history_and_generation_semantics():
    record = json.loads((ROOT / "fixtures/synthetic/job.json").read_text())
    job = v1.StockfishJob.model_validate(record["payload"])
    assert stockfish_input_digest(job) == job.input_digest
    assert stockfish_input_digest(job.model_copy(update={"execution_generation": 2})) == job.input_digest
    assert stockfish_input_digest(job.model_copy(update={"max_nodes": None})) != job.input_digest
    unicode_job = job.model_dump()
    unicode_job["workspace_id"] = "é-棋-😀"
    expected = json.loads((ROOT / "fixtures/digest-vectors.json").read_text())
    assert stockfish_input_digest(unicode_job) == expected["unicode"]


def test_client_reports_loss_and_does_not_retry():
    async def scenario():
        command = [sys.executable, "-u", "-c", 'import sys,json; print(json.dumps({"event":"ready","identity":{"engine_version":"fixture"}}),file=sys.stderr,flush=True)']
        with pytest.raises(WorkerSessionLost):
            async with StockfishClient(command) as client:
                async for _ in client.records():
                    pass
    asyncio.run(scenario())


def test_synthetic_examples_validate_without_becoming_actual_evidence():
    for file in (ROOT / "fixtures/synthetic").glob("*.json"):
        record = json.loads(file.read_text())
        getattr(v1, record["model"]).model_validate(record["payload"])


def _consumer():
    import importlib.util
    spec = importlib.util.spec_from_file_location("independent_consumer", ROOT / "tests/contracts/consume_observed.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("fixture_directory", ["observed", "qualified"])
def test_B_installed_consumer(fixture_directory):
    exchange = json.loads((ROOT / "fixtures" / fixture_directory / "exchange.json").read_text())
    result = _consumer().consume(exchange)
    assert result["results"] > result["distinct_accepted_executions"] > 0
    assert result["acknowledgements"] > 0


def test_independent_consumer_rejects_illegal_pv_and_cancellation_identity():
    import copy
    exchange = json.loads((ROOT / "fixtures/observed/exchange.json").read_text())
    illegal = copy.deepcopy(exchange)
    result = next(record for record in illegal["records"] if record["model"] == "StockfishResult")
    result["payload"]["variations"][0]["moves"] = ["a1a8"]
    with pytest.raises(AssertionError):
        _consumer().consume(illegal)
    invalid_ack = copy.deepcopy(exchange)
    acknowledgement = next(record for record in invalid_ack["records"] if record["model"] == "CancellationAcknowledgement")
    acknowledgement["payload"]["execution_generation"] += 1
    with pytest.raises(AssertionError):
        _consumer().consume(invalid_ack)


def test_startup_timeout_is_typed_session_loss_and_reaps_its_launcher():
    async def scenario():
        client = StockfishClient([sys.executable, "-u", "-c", "import sys; sys.stdin.read()"], startup_timeout=.05)
        with pytest.raises(WorkerSessionLost, match="startup deadline"):
            async with client:
                pass
        assert client.process.returncode is not None
    asyncio.run(scenario())


@pytest.mark.parametrize("explicit", [False, True], ids=["default_whitelist", "explicit_mapping"])
def test_B_client_environment_isolation(monkeypatch, explicit):
    import os
    for key in ("TEMPO_PRIVATE_C_CONFIG", "PGPASSWORD", "DATABASE_URL", "PROVIDER_API_KEY"):
        monkeypatch.setenv(key, "synthetic-secret-canary")
    script = '''import os,sys,json
print(json.dumps({"event":"ready","identity":{"engine_version":"fixture"}}),file=sys.stderr,flush=True)
record=json.loads(sys.stdin.readline())["payload"]
private=("TEMPO_PRIVATE_C_CONFIG","PGPASSWORD","DATABASE_URL","PROVIDER_API_KEY")
safe=not any(key in os.environ for key in private)
if len(sys.argv)>1:safe=safe and os.environ.get("TEST_ALLOWED")=="yes"
fields={key:record[key] for key in ("contract_version","workspace_id","job_id","request_revision","execution_generation")}
print(json.dumps({"model":"JobStatus","payload":{**fields,"status":"failed","retryable":False,"reason":"isolated" if safe else "environment_leak"}}),flush=True)
sys.stdin.read()
'''
    async def scenario():
        command = [sys.executable, "-u", "-c", script, *(["explicit"] if explicit else [])]
        mapping = {"PATH": os.environ.get("PATH", ""), "LANG": "C", "TEST_ALLOWED": "yes"} if explicit else None
        async with StockfishClient(command, env=mapping) as client:
            if mapping is not None:
                assert client.env == mapping
                mapping["TEST_ALLOWED"] = "mutated-after-start"
            payload = json.loads((ROOT / "fixtures/synthetic/job.json").read_text())["payload"]
            await client.send_job(v1.StockfishJob.model_validate(payload))
            async for record in client.records():
                assert record.reason == "isolated"
                break
    asyncio.run(scenario())
