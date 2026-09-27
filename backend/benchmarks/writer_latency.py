"""Measure the local SQLite writer with disposable data and bounded contention."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import logging
import platform
import re
import sqlite3
import statistics
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time

from app import database as database_module
from app.services.database_executor import DatabaseWriter


class WriterPhaseCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.phases_by_label: dict[str, dict[str, float]] = {}

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        label_match = re.search(r"\blabel=([^ ]+)", message)
        if not label_match or not message.startswith("database write "):
            return
        self.phases_by_label[label_match.group(1)] = {
            name: float(seconds) * 1000
            for name, seconds in re.findall(r"\b([a-z_]+)=([0-9.]+)s", message)
        }


def summary(samples_ms: list[float]) -> dict:
    ordered = sorted(samples_ms)
    return {
        "samples_ms": samples_ms,
        "p50_ms": statistics.median(samples_ms),
        "p95_ms": ordered[max(0, (len(ordered) * 95 + 99) // 100 - 1)],
    }


def write_sample(writer: DatabaseWriter, label: str) -> float:
    started_at = time.perf_counter()
    writer.submit_foreground_write(
        lambda connection: connection.execute("INSERT INTO samples(label) VALUES (?)", (label,)),
        label=label,
    )
    return (time.perf_counter() - started_at) * 1000


def phase_summaries(phase_capture: WriterPhaseCapture, label_prefix: str) -> dict:
    records = [phases for label, phases in phase_capture.phases_by_label.items()
               if label.startswith(label_prefix)]
    return {
        phase_name: summary([record[phase_name] for record in records])
        for phase_name in (
            "queue_wait", "gate_wait", "compatibility_lock_wait", "begin",
            "operation", "commit", "hold", "total",
        )
    }


def benchmark_writer(writer: DatabaseWriter, database_path: Path,
                     phase_capture: WriterPhaseCapture) -> dict:
    for warmup_index in range(5):
        write_sample(writer, f"warmup-{warmup_index}")

    idle_samples_ms = [write_sample(writer, f"idle-{index}") for index in range(30)]
    queued_runs = []
    for run_index in range(7):
        simultaneous_start = threading.Barrier(16)
        with ThreadPoolExecutor(max_workers=16) as workers:
            futures = [workers.submit(
                lambda position=position: (
                    simultaneous_start.wait(),
                    write_sample(writer, f"queued-{run_index}-{position}"),
                )[1]
            ) for position in range(16)]
            queued_runs.append([future.result(timeout=10) for future in futures])

    locked_samples_ms = []
    for sample_index in range(7):
        lock_owner = sqlite3.connect(database_path)
        lock_owner.execute("BEGIN IMMEDIATE")
        with ThreadPoolExecutor(max_workers=1) as worker:
            future = worker.submit(write_sample, writer, f"lock-{sample_index}")
            time.sleep(0.02)
            lock_owner.rollback()
            locked_samples_ms.append(future.result(timeout=10))
        lock_owner.close()

    return {
        "idle_foreground": {
            "latency": summary(idle_samples_ms),
            "writer_phases": phase_summaries(phase_capture, "idle-"),
        },
        "queued_foreground": {
            "workers": 16,
            "runs": 7,
            "per_run_samples_ms": queued_runs,
            "latency": summary([sample for run in queued_runs for sample in run]),
            "writer_phases": phase_summaries(phase_capture, "queued-"),
        },
        "external_writer_lock": {
            "lock_hold_ms": 20,
            "latency": summary(locked_samples_ms),
            "writer_phases": phase_summaries(phase_capture, "lock-"),
        },
    }


def main() -> None:
    original_database_path = database_module.DB_PATH
    with TemporaryDirectory(prefix="tempo-writer-benchmark-") as directory:
        database_path = Path(directory) / "writer.sqlite3"
        with sqlite3.connect(database_path) as setup_connection:
            setup_connection.execute("CREATE TABLE samples (id INTEGER PRIMARY KEY, label TEXT)")
        database_module.DB_PATH = database_path
        writer = DatabaseWriter()
        writer_logger = logging.getLogger("tempo.writer")
        original_log_level = writer_logger.level
        phase_capture = WriterPhaseCapture()
        writer_logger.addHandler(phase_capture)
        writer_logger.setLevel(logging.INFO)
        try:
            writer.start()
            cases = benchmark_writer(writer, database_path, phase_capture)
        finally:
            writer.stop()
            writer_logger.removeHandler(phase_capture)
            writer_logger.setLevel(original_log_level)
            database_module.DB_PATH = original_database_path

    report = {
        "schema_version": 1,
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fixture": "disposable-sqlite-inserts-v1",
        "environment": {"python": sys.version.split()[0], "platform": platform.platform()},
        "method": "Foreground writes through DatabaseWriter; external lock held for 20 ms; excludes API/network work",
        "cases": cases,
    }
    output_path = Path("test-results/performance/sqlite-writer-benchmark.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        name: round(case["latency"]["p50_ms"], 2)
        for name, case in cases.items()
    }))
    print(output_path)


if __name__ == "__main__":
    main()
