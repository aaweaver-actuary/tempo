"""Executable finite fixture caller; does not implement C's durable scheduler."""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
import time
from tempo_analysis_jobs_contracts import v1
from tempo_stockfish_client import StockfishClient, stockfish_input_digest


async def run_fixture(client, fixture, template, record_sink=lambda _: None):
    beginning = time.monotonic()
    completions = []
    for item in fixture:
        payload = {**template, **item["job_changes"], "job_id": item["job_id"], "engine_version": client.identity["engine_version"]}
        payload["input_digest"] = stockfish_input_digest(payload)
        job = v1.StockfishJob.model_validate(payload)
        admitted = time.monotonic()
        record_sink({"model": "StockfishJob", "payload": job.model_dump(mode="json")})
        await client.send_job(job)
        async def receive():
            async for record in client.records():
                record_sink({"model": type(record).__name__, "payload": record.model_dump(mode="json")})
                if record.job_id != job.job_id:
                    continue
                if isinstance(record, v1.StockfishResult):
                    return record
                if isinstance(record, v1.JobStatus) and record.status == "failed":
                    raise AssertionError(record)
        result = await asyncio.wait_for(receive(), 20)
        completions.append({"class": item["class"], "original_age_seconds": item["original_age_seconds"], "job_id": job.job_id, "queue_wait_ms": (admitted-beginning)*1000, "end_to_end_ms": (time.monotonic()-admitted)*1000, "actual_depth": result.variations[0].depth})
    assert len(completions) == len(fixture) and completions
    return {"useful_completions":len(completions),"completions":completions,"scope":"finite fixture caller ordering; no durable C scheduler or live Study measurement"}


async def main(arguments):
    root=Path(__file__).resolve().parent
    fixture=json.loads(Path(arguments.fixture).read_text())
    template=json.loads((root/"fixtures/synthetic/job.json").read_text())["payload"]
    async with StockfishClient(json.loads(arguments.command_json)) as client:
        report=await run_fixture(client,fixture,template)
    Path(arguments.report).write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({"success":True,"useful_completions":report["useful_completions"]}))


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--fixture",default=str(Path(__file__).resolve().parent/"fixtures/backlog.json"))
    parser.add_argument("--command-json",required=True)
    parser.add_argument("--report",required=True)
    asyncio.run(main(parser.parse_args()))
