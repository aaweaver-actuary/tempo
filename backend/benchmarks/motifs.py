"""Benchmark the existing motif parity corpus at increasing analysis batch sizes."""

from __future__ import annotations

import json
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import chess

from app.services.motif_detectors import classify_candidate_lines


FIXTURE_PATH = Path("tests/fixtures/motif-parity.json")


def percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    return ordered[max(0, int(len(ordered) * fraction + 0.999999) - 1)]


def measure_batch(fixtures: list[dict], repeat_count: int) -> dict[str, float | int]:
    parse_seconds = 0.0
    detector_seconds = 0.0
    evidence_count = 0
    started_at = time.perf_counter()
    for _ in range(repeat_count):
        for fixture in fixtures:
            parse_started_at = time.perf_counter()
            position = chess.Board(fixture["fen"])
            parse_seconds += time.perf_counter() - parse_started_at
            detector_started_at = time.perf_counter()
            evidence_count += len(classify_candidate_lines(
                position, fixture.get("played_move"), fixture["candidates"]
            ))
            detector_seconds += time.perf_counter() - detector_started_at
    return {
        "total_ms": (time.perf_counter() - started_at) * 1000,
        "fen_parse_ms": parse_seconds * 1000,
        "detector_ms": detector_seconds * 1000,
        "evidence_count": evidence_count,
    }


def main() -> None:
    fixtures = json.loads(FIXTURE_PATH.read_text())
    cases = []
    for name, repeat_count in (("small", 1), ("typical", 10), ("large", 100), ("stress", 400)):
        measure_batch(fixtures, 1)
        samples = [measure_batch(fixtures, repeat_count) for _ in range(7)]
        total_samples = [sample["total_ms"] for sample in samples]
        cases.append({
            "name": name,
            "positions": len(fixtures) * repeat_count,
            "candidate_lines": sum(len(item["candidates"]) for item in fixtures) * repeat_count,
            "pv_plies": sum(len(candidate["pv"]) for item in fixtures for candidate in item["candidates"]) * repeat_count,
            "evidence_count": samples[0]["evidence_count"],
            "samples": samples,
            "p50_total_ms": statistics.median(total_samples),
            "p95_total_ms": percentile(total_samples, 0.95),
            "p50_fen_parse_ms": statistics.median(sample["fen_parse_ms"] for sample in samples),
            "p50_detector_ms": statistics.median(sample["detector_ms"] for sample in samples),
        })
    report = {
        "schema_version": 1,
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fixture": "repeated-motif-parity-corpus-v1",
        "fixture_ids": [fixture["id"] for fixture in fixtures],
        "environment": {"python": sys.version.split()[0], "platform": platform.platform()},
        "cases": cases,
    }
    output_path = Path("test-results/performance/motif-detection-benchmark.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({case["name"]: round(case["p50_total_ms"], 1) for case in cases}))
    print(output_path)


if __name__ == "__main__":
    main()
