import asyncio
import importlib
import json
import os
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


@pytest.mark.parametrize('refusal', ['active_conflict', 'completed_conflict', 'cancellation_conflict', 'cancellation_capacity'])
def test_B_installed_client_command_rejection_is_typed_session_loss(refusal):
    runtime = Path(os.environ['TEMPO_STOCKFISH_TEST_RUNTIME'])
    node = os.environ['TEMPO_STOCKFISH_TEST_NODE']
    payload = json.loads((ROOT / 'fixtures/synthetic/job.json').read_text())['payload']
    payload.update(max_depth=4, max_nodes=100000 if refusal == 'completed_conflict' else 999999, search_moves=[], multi_pv=1)
    payload['input_digest'] = stockfish_input_digest(payload)
    original = v1.StockfishJob.model_validate(payload)
    command = [node, str(ROOT / 'tests/contracts/rejection_server.mjs'), str(runtime), original.engine_version]

    async def scenario():
        records = []
        changed = asyncio.Event()
        async with StockfishClient(command) as client:
            async def collect():
                try:
                    async for value in client.records():
                        records.append(value); changed.set()
                except WorkerSessionLost as error:
                    changed.set()
                    return error
            collector = asyncio.create_task(collect())
            async def until(predicate):
                while not any(predicate(value) for value in records):
                    changed.clear()
                    if collector.done():
                        raise AssertionError('session ended before expected receipt')
                    await changed.wait()
            await client.send_job(original)
            await until(lambda value: isinstance(value, v1.JobStatus) and value.status == ('completed' if refusal == 'completed_conflict' else 'running'))
            if refusal.endswith('conflict') and refusal != 'cancellation_conflict':
                conflict = original.model_dump(); conflict['max_depth'] = 5
                conflict['input_digest'] = stockfish_input_digest(conflict)
                await client.send_job(v1.StockfishJob.model_validate(conflict))
                reason = 'identity_payload_conflict'
            else:
                fields = {key: payload[key] for key in ('contract_version','workspace_id','job_id','request_revision','execution_generation')}
                if refusal == 'cancellation_conflict':
                    bound = v1.CancellationRequest(**{**fields, 'job_id': 'future-target'}, cancellation_id='bound-id')
                    await client.send_cancellation(bound)
                    await until(lambda value: isinstance(value, v1.CancellationAcknowledgement) and value.cancellation_id == 'bound-id')
                    await client.send_cancellation(v1.CancellationRequest(**fields, cancellation_id='bound-id'))
                    reason = 'cancellation_id_conflict'
                else:
                    for index in range(64):
                        await client.send_cancellation(v1.CancellationRequest(**fields, cancellation_id=f'retained-{index}'))
                    await client.send_cancellation(v1.CancellationRequest(**fields, cancellation_id='unretained'))
                    reason = 'cancellation_capacity_exhausted'
            assert isinstance(await collector, WorkerSessionLost)
            await asyncio.wait_for(client.process.wait(), 3)
            assert client.process.returncode == 1
            assert not any(isinstance(value, v1.JobStatus) and value.status == 'failed' for value in records)
            assert not any(isinstance(value, v1.CancellationAcknowledgement) and value.cancellation_id == 'unretained' for value in records)
            assert any(event.get('reason') == reason for event in client.diagnostics)
            assert 'synthetic-secret-canary' not in json.dumps(list(client.diagnostics))
        # A new session receives only the explicitly advanced generation.
        async with StockfishClient(command) as restarted:
            retry = original.model_copy(update={'execution_generation': 2})
            await restarted.send_job(retry)
            async for value in restarted.records():
                assert value.execution_generation == 2
                if isinstance(value, v1.JobStatus) and value.status == 'running':
                    break
            await restarted.send_cancellation(v1.CancellationRequest(
                contract_version=1, workspace_id=retry.workspace_id, job_id=retry.job_id,
                request_revision=retry.request_revision, execution_generation=2, cancellation_id='retry-cancel'))
            async for value in restarted.records():
                assert value.execution_generation == 2
                if isinstance(value, v1.CancellationAcknowledgement) and value.status in {'terminated', 'already_completed'}:
                    break
    asyncio.run(asyncio.wait_for(scenario(), 10))


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


@pytest.mark.parametrize("fixture_directory", ["observed", "qualified", "repaired"])
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


def test_B_independent_consumer_rejects_false_terminal_execution_status():
    exchange = json.loads((ROOT / 'fixtures/repaired/exchange.json').read_text())
    index = next(index for index, record in enumerate(exchange['records']) if record['model'] == 'StockfishResult')
    result = exchange['records'][index]['payload']
    fields = {key:result[key] for key in ('contract_version','workspace_id','job_id','request_revision','execution_generation')}
    exchange['records'].insert(index+1, {'model':'JobStatus','payload':{**fields,'status':'failed','retryable':False,'reason':'identity_payload_conflict'}})
    with pytest.raises(AssertionError, match='failed/cancelled status after an accepted result'):
        _consumer().consume(exchange)


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
