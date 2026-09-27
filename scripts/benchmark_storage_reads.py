"""Preliminary in-process SQLite/PostgreSQL API read comparison.

This is a rehearsal microbenchmark, not the full cutover benchmark: it does not
include HTTP transport, Celery backlog, writes, CPU, or disk measurements.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
from pathlib import Path
import sys
import time
from urllib.parse import quote

import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


async def sample_reads(app, store: str, concurrent_clients: int, samples: list[dict]) -> None:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    semaphore = asyncio.Semaphore(concurrent_clients)
    async with httpx.AsyncClient(transport=transport, base_url="http://tempo.local") as client:
        for route in ("/api/settings", "/api/queue/today", "/api/games/summary"):
            async def request_one(sample_number: int) -> None:
                async with semaphore:
                    started = time.perf_counter()
                    response = await client.get(route)
                    samples.append({
                        "store": store,
                        "clients": concurrent_clients,
                        "route": route,
                        "sample": sample_number,
                        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                        "status": response.status_code,
                    })

            await asyncio.gather(*(request_one(index) for index in range(32)))


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


async def main_async(arguments) -> None:
    os.environ["TEMPO_DB_PATH"] = str(arguments.sqlite_path)
    from app import database
    from app.main import app

    database.DB_PATH = arguments.sqlite_path
    samples: list[dict] = []
    for clients in (1, 4, 16):
        await sample_reads(app, "sqlite", clients, samples)
    reader_password = arguments.pg_reader_password_file.read_text().strip()
    os.environ["TEMPO_DATABASE_READ_URL"] = (
        f"postgresql://tempo_reader:{quote(reader_password)}@{arguments.pg_host}/tempo"
    )
    for clients in (1, 4, 16):
        await sample_reads(app, "postgres", clients, samples)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    with arguments.output.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["store", "clients", "route", "sample", "latency_ms", "status"])
        writer.writeheader()
        writer.writerows(samples)
    for store in ("sqlite", "postgres"):
        for clients in (1, 4, 16):
            for route in ("/api/settings", "/api/queue/today", "/api/games/summary"):
                selected = [item for item in samples if item["store"] == store and item["clients"] == clients and item["route"] == route]
                durations = [item["latency_ms"] for item in selected]
                failures = sum(item["status"] != 200 for item in selected)
                print(
                    f"{store:8} clients={clients:2} {route:20} "
                    f"p50={percentile(durations, .50):8.1f} "
                    f"p95={percentile(durations, .95):8.1f} "
                    f"p99={percentile(durations, .99):8.1f} failures={failures}"
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite_path", type=Path)
    parser.add_argument("--pg-reader-password-file", type=Path, required=True)
    parser.add_argument("--pg-host", default="127.0.0.1:55432")
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main_async(parser.parse_args()))
