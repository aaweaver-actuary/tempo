"""Executable finite fixture caller; does not implement C's durable scheduler."""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
import time
import uuid
from tempo_analysis_jobs_contracts import v1
from tempo_stockfish_client import StockfishClient, stockfish_input_digest


async def run_fixture(client, fixture, template, record_sink=lambda _: None):
    if not fixture:
        raise ValueError("The finite backlog must be nonempty")
    beginning = time.monotonic()
    completions = []

    def make_job(item, **changes):
        payload = {**template, **item["job_changes"], "job_id": item["job_id"],
                   "engine_version": client.identity["engine_version"], **changes}
        payload["input_digest"] = stockfish_input_digest(payload)
        return v1.StockfishJob.model_validate(payload)

    async def send(job):
        record_sink({"model": "StockfishJob", "payload": job.model_dump(mode="json")})
        await client.send_job(job)

    async def receive(predicate):
        async def wait_for_record():
            async for record in client.records():
                record_sink({"model": type(record).__name__, "payload": record.model_dump(mode="json")})
                if predicate(record):
                    return record
        return await asyncio.wait_for(wait_for_record(), 20)

    # A finite probe makes busy, cancellation, recovery and retry observable.
    # Its caller metadata never enters a shared job. Remaining work is serial.
    probe = make_job({"job_id": "fixture-cancel-probe", "job_changes": {}},
                     max_depth=128, max_nodes=None, search_moves=[], multi_pv=1,
                     constraints={"max_wall_time_ms": 10000, "max_memory_mb": 512})
    old_pid = next(event["pid"] for event in reversed(client.diagnostics)
                   if event.get("event") == "ready")
    await send(probe)
    await receive(lambda record: isinstance(record, v1.JobStatus) and
                  record.job_id == probe.job_id and record.status == "running")
    first_job = make_job(fixture[0])
    await send(first_job)
    busy = await receive(lambda record: isinstance(record, v1.JobStatus) and
                         record.job_id == first_job.job_id and record.status == "failed")
    assert busy.reason == "worker_busy" and busy.retryable
    cancellation = v1.CancellationRequest(
        contract_version=1, workspace_id=probe.workspace_id, job_id=probe.job_id,
        request_revision=probe.request_revision, execution_generation=probe.execution_generation,
        cancellation_id=str(uuid.uuid4()))
    record_sink({"model": "CancellationRequest", "payload": cancellation.model_dump(mode="json")})
    cancelled_at = time.monotonic()
    await client.send_cancellation(cancellation)
    acknowledgement = await receive(lambda record: isinstance(record, v1.CancellationAcknowledgement)
                                    and record.cancellation_id == cancellation.cancellation_id
                                    and record.status == "terminated")
    terminated_ms = (time.monotonic() - cancelled_at) * 1000
    recovery_deadline = time.monotonic() + 35
    while not any(event.get("event") == "ready" and event.get("pid") != old_pid
                  for event in client.diagnostics):
        if time.monotonic() >= recovery_deadline:
            raise TimeoutError("Finite backlog probe capacity did not recover")
        await asyncio.sleep(.01)
    recovery_ms = (time.monotonic() - cancelled_at) * 1000

    for index, item in enumerate(fixture):
        job = first_job.model_copy(update={"execution_generation": first_job.execution_generation + 1}) if index == 0 else make_job(item)
        dispatched = time.monotonic()
        await send(job)
        result = await receive(lambda record: record.job_id == job.job_id
                               and record.execution_generation == job.execution_generation
                               and (isinstance(record, v1.StockfishResult)
                                    or isinstance(record, v1.JobStatus) and record.status == "failed"))
        assert isinstance(result, v1.StockfishResult), result
        completions.append({"class": item["class"], "original_age_seconds": item["original_age_seconds"],
                            "job_id": job.job_id, "execution_generation": job.execution_generation,
                            "queue_wait_ms": (dispatched - beginning) * 1000,
                            "end_to_end_ms": (time.monotonic() - dispatched) * 1000,
                            "actual_depth": result.variations[0].depth})
    assert len(completions) == len(fixture)
    foreground = [item["end_to_end_ms"] for item in completions if item["class"] == "foreground-like"]
    return {"useful_completions": len(completions), "waiting_jobs": len(fixture),
            "busy_responses": 1, "retry_attempts": 1, "cancelled_probes": 1,
            "cancellation": {"acknowledgement": acknowledgement.model_dump(mode="json"),
                             "termination_ms": terminated_ms, "capacity_recovery_ms": recovery_ms},
            "foreground_like_latency_ms": foreground, "completions": completions,
            "scope": "finite fixture caller ordering and one probe; no durable C scheduler or live Study measurement"}


async def main(arguments):
    root = Path(__file__).resolve().parent
    fixture = json.loads(Path(arguments.fixture).read_text())
    template = json.loads((root / "fixtures/synthetic/job.json").read_text())["payload"]
    async with StockfishClient(json.loads(arguments.command_json)) as client:
        report = await run_fixture(client, fixture, template)
    Path(arguments.report).write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"success": True, "useful_completions": report["useful_completions"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", default=str(Path(__file__).resolve().parent / "fixtures/backlog.json"))
    parser.add_argument("--command-json", required=True)
    parser.add_argument("--report", required=True)
    asyncio.run(main(parser.parse_args()))
