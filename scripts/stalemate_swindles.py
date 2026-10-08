#!/usr/bin/env python3
"""Offline mining/build CLI. Never import app.main or connect to a database."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.stalemate_swindles import (  # noqa: E402
    MiningFilters, build_bundle, json_bytes, mine_candidates, new_counts,
    select_candidates, selected_distribution, validate_month,
)


class ArchiveCancellation(BaseException):
    def __init__(self, received_signal: int):
        self.received_signal = received_signal


class ArchiveCancellationState:
    def __init__(self):
        self.received_signal: int | None = None


@contextmanager
def cli_cancellation_signals():
    """Retain cancellation even if an asynchronous exception is suppressed."""
    cancellation_signals = (signal.SIGINT, signal.SIGTERM)
    previous_handlers = {received_signal: signal.getsignal(received_signal)
                         for received_signal in cancellation_signals}
    cancellation = ArchiveCancellationState()

    def cancel_command(received_signal, _frame):
        if cancellation.received_signal is None:
            cancellation.received_signal = received_signal
            raise ArchiveCancellation(received_signal)
        # Repeated signals defer to the first bounded cleanup unwind.

    try:
        for received_signal in cancellation_signals:
            signal.signal(received_signal, cancel_command)
        yield cancellation
    finally:
        for received_signal, previous_handler in previous_handlers.items():
            signal.signal(received_signal, previous_handler)
        if cancellation.received_signal is not None:
            raise ArchiveCancellation(cancellation.received_signal)


def positive_integer(value: str) -> int:
    parsed_value = int(value)
    if parsed_value < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed_value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def publish_files(outputs: list[tuple[Path, Path]]) -> None:
    """Stage before replacing; restore previous files if any replacement fails.

    Metadata/manifest is last and is the verifiable commit marker. Readers
    reject a missing marker or mismatched payload hash after a hard crash.
    """
    previous_files = {}
    published_paths = []
    try:
        for _, destination in outputs:
            if destination.exists():
                descriptor, backup_name = tempfile.mkstemp(prefix=destination.name + ".", suffix=".partial", dir=destination.parent)
                os.close(descriptor)
                backup_path = Path(backup_name)
                previous_files[destination] = backup_path
                shutil.copyfile(destination, backup_path)
            else:
                previous_files[destination] = None
        for staged_path, destination in outputs:
            os.replace(staged_path, destination)
            published_paths.append(destination)
    except BaseException:
        for destination in reversed(published_paths):
            previous_file = previous_files[destination]
            if previous_file is None:
                destination.unlink(missing_ok=True)
            else:
                os.replace(previous_file, destination)
        raise
    finally:
        for previous_file in previous_files.values():
            if previous_file is not None:
                previous_file.unlink(missing_ok=True)


@contextmanager
def staged_path(destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(prefix=destination.name + ".", suffix=".partial", dir=destination.parent)
    os.close(descriptor)
    temporary_path = Path(filename)
    try:
        yield temporary_path
    finally:
        temporary_path.unlink(missing_ok=True)


class ArchiveStream:
    """Own only this CLI's curl/zstd processes and a bounded compressed-byte pump."""

    TERMINATION_GRACE_SECONDS = 5

    def __init__(self, source_url: str, expected_sha256: str, cancellation: ArchiveCancellationState | None = None):
        self.cancellation = cancellation
        self.source_url = source_url
        self.expected_sha256 = expected_sha256
        self.digest = hashlib.sha256()
        self.pump_errors: list[Exception] = []
        self.downloader = None
        self.decompressor = None
        self.pump = None
        self.text_stream = None
        self.monitors: list[threading.Thread] = []
        self.failures: list[str] = []
        self.lifecycle_lock = threading.Lock()
        self.stopping = threading.Event()
        self.stopped = threading.Event()
        self.input_forwarded = threading.Event()

    def stop(self, failure: str | None = None):
        """One caller signals/reaps both children; other callers never join it."""
        with self.lifecycle_lock:
            if self.stopping.is_set():
                return
            for stage, process in (("curl", self.downloader), ("zstd", self.decompressor)):
                if process is not None and process.poll() not in (None, 0):
                    self.failures.append(f"{stage} exited with {process.returncode}")
            if failure is not None:
                self.failures.append(failure)
            self.stopping.set()
        try:
            # Signal every stage before waiting: either pipe may be backpressured.
            for process in (self.downloader, self.decompressor):
                if process is not None and process.poll() is None:
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        pass
            for process in (self.downloader, self.decompressor):
                if process is not None:
                    try:
                        process.wait(timeout=self.TERMINATION_GRACE_SECONDS)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=self.TERMINATION_GRACE_SECONDS)
        finally:
            self.stopped.set()

    def monitor_process(self, stage: str, process):
        while not self.stopping.is_set():
            if self.cancellation is not None and self.cancellation.received_signal is not None:
                self.stop()
                return
            try:
                result = process.wait(timeout=self.TERMINATION_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                continue
            if result:
                self.stop(f"{stage} exited with {result}")
            elif stage == "zstd" and not self.input_forwarded.is_set():
                self.stop("zstd exited before the compressed source was fully forwarded")
            return

    def join_workers(self):
        for worker in (self.pump, *self.monitors):
            if worker is not None and worker.ident is not None:
                worker.join(timeout=self.TERMINATION_GRACE_SECONDS)
                if worker.is_alive():
                    raise ValueError(f"Archive pipeline failed: {worker.name} did not stop")

    def raise_pipeline_failure(self):
        if self.failures:
            raise ValueError(f"Archive pipeline failed: curl={self.downloader.returncode}, "
                             f"zstd={self.decompressor.returncode}, pump_errors={len(self.pump_errors)}; "
                             + "; ".join(self.failures))

    def __enter__(self):
        try:
            self.downloader = subprocess.Popen(["curl", "--fail", "--location", "--silent", "--show-error", self.source_url], stdout=subprocess.PIPE)
            self.decompressor = subprocess.Popen(["zstd", "-dc"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            def transfer_compressed_bytes():
                try:
                    while not self.stopping.is_set() and (compressed_chunk := self.downloader.stdout.read(1024 * 1024)):
                        self.digest.update(compressed_chunk)
                        self.decompressor.stdin.write(compressed_chunk)
                    if not self.stopping.is_set():
                        self.decompressor.stdin.flush()
                        # Mark forwarding before sending EOF, which permits zstd's normal exit.
                        self.input_forwarded.set()
                except Exception as error:
                    if not self.stopping.is_set():
                        self.pump_errors.append(error)
                        self.stop(f"compressed-byte pump failed: {error}")
                finally:
                    try:
                        self.decompressor.stdin.close()
                    except (BrokenPipeError, OSError) as error:
                        if not self.stopping.is_set():
                            self.pump_errors.append(error)
                            self.stop(f"compressed-byte pump close failed: {error}")
            self.pump = threading.Thread(target=transfer_compressed_bytes, name="stalemate-archive-pump")
            # Process-directed signals must wake the main thread's blocked read.
            # Workers inherit this mask; restore the main mask after all start.
            previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
            try:
                self.pump.start()
                for stage, process in (("curl", self.downloader), ("zstd", self.decompressor)):
                    monitor = threading.Thread(target=self.monitor_process, args=(stage, process), name=f"stalemate-archive-{stage}")
                    self.monitors.append(monitor)
                    monitor.start()
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
            self.text_stream = io.TextIOWrapper(self.decompressor.stdout, encoding="utf-8", errors="strict")
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def verify_complete(self):
        if not self.input_forwarded.is_set():
            self.stop("decompressed output ended before compressed source completion")
        for stage, process in (("curl", self.downloader), ("zstd", self.decompressor)):
            try:
                result = process.wait(timeout=self.TERMINATION_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                self.stop(f"{stage} did not exit after stream EOF")
                break
            if result:
                self.stop(f"{stage} exited with {result}")
        self.join_workers()
        self.raise_pipeline_failure()
        if self.digest.hexdigest() != self.expected_sha256:
            raise ValueError("Compressed source SHA-256 differs from the published checksum")

    def finish_sampling(self):
        self.close()
        self.raise_pipeline_failure()

    def close(self):
        self.stop()
        if not self.stopped.wait(timeout=4 * self.TERMINATION_GRACE_SECONDS):
            raise ValueError("Archive pipeline failed: owned process shutdown did not finish")
        self.join_workers()
        if self.text_stream is not None:
            self.text_stream.close()
        for process in (self.downloader, self.decompressor):
            if process is not None:
                for pipe in (process.stdin, process.stdout):
                    if pipe is not None:
                        pipe.close()

    def __exit__(self, *_):
        self.close()


def mine_to_file(arguments, stream, source: dict, archive: ArchiveStream | None = None):
    filters = MiningFilters(arguments.min_material_deficit, arguments.min_rating,
                            tuple(sorted(set(arguments.speeds.lower().split(",")))), arguments.include_bots)
    counts = new_counts()
    output = Path(arguments.output)
    metadata_path = Path(str(output) + ".metadata.json")
    limited = arguments.max_games is not None or arguments.stop_after_candidates is not None
    with staged_path(output) as staged_candidates, staged_path(metadata_path) as staged_metadata:
        with staged_candidates.open("wb") as candidate_file:
            for candidate in mine_candidates(stream, arguments.source_month, filters, counts,
                    strict=arguments.strict, max_games=arguments.max_games, stop_after_candidates=arguments.stop_after_candidates,
                    progress_every=arguments.progress_every,
                    progress=lambda progress_counts: print(json.dumps(progress_counts, sort_keys=True), file=sys.stderr, flush=True)):
                candidate_file.write(json_bytes(candidate))
        if archive is not None and not limited:
            archive.verify_complete()
            source.update({"sha256": archive.digest.hexdigest(), "sha256_verified": True})
        elif archive is not None:
            archive.finish_sampling()
        metadata = {"schema_version": 1, "generator_version": 1, "source_month": arguments.source_month,
            "source": source, "filters": asdict(filters), "limits": {"max_games": arguments.max_games, "stop_after_candidates": arguments.stop_after_candidates},
            "completion": {"complete": not limited, "reason": "sampling_mode" if limited else "input_eof"},
            "counts": dict(counts), "candidate_sha256": file_sha256(staged_candidates)}
        staged_metadata.write_bytes(json_bytes(metadata))
        publish_files([(staged_candidates, output), (staged_metadata, metadata_path)])
    print(json.dumps(metadata, sort_keys=True), file=sys.stderr, flush=True)


def run_mine(arguments):
    validate_month(arguments.source_month)
    if bool(arguments.source_url) != bool(arguments.source_sha256):
        raise ValueError("source-url and source-sha256 must be supplied together")
    if arguments.source_url:
        if arguments.input is not None:
            raise ValueError("source-url cannot be combined with input")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", arguments.source_sha256):
            raise ValueError("source-sha256 must be a 64-character SHA-256")
        expected_filename = f"lichess_db_standard_rated_{arguments.source_month}.pgn.zst"
        if Path(urlparse(arguments.source_url).path).name != expected_filename:
            raise ValueError("Archive filename must match source-month")
        source = {"filename": expected_filename, "url": arguments.source_url,
                  "expected_sha256": arguments.source_sha256.lower(), "sha256": None, "sha256_verified": False}
        with ArchiveStream(arguments.source_url, arguments.source_sha256.lower(),
                           getattr(arguments, "cancellation", None)) as archive:
            mine_to_file(arguments, archive.text_stream, source, archive)
    else:
        input_path = arguments.input or "-"
        source = {"filename": None if input_path == "-" else Path(input_path).name,
                  "url": None, "expected_sha256": None, "sha256": None, "sha256_verified": False}
        if input_path == "-":
            mine_to_file(arguments, sys.stdin, source)
        else:
            with open(input_path, encoding="utf-8") as input_stream:
                mine_to_file(arguments, input_stream, source)


def load_candidates(paths: list[str], receipts: list[dict]):
    verified_candidate_hashes: set[str] = set()
    for filename in paths:
        path = Path(filename)
        receipt = json.loads(Path(str(path) + ".metadata.json").read_text(encoding="utf-8"))
        if receipt.get("schema_version") != 1 or receipt.get("candidate_sha256") != file_sha256(path):
            raise ValueError(f"Candidate file/metadata mismatch: {path}")
        candidate_sha256 = receipt["candidate_sha256"]
        if candidate_sha256 in verified_candidate_hashes:
            raise ValueError(f"Candidate inputs contain duplicate mined output: {path}")
        verified_candidate_hashes.add(candidate_sha256)
        receipts.append(receipt)
        with path.open(encoding="utf-8") as candidate_file:
            observed_count = 0
            for line_number, candidate_line in enumerate(candidate_file, 1):
                try:
                    candidate = json.loads(candidate_line)
                    if candidate["source_month"] != receipt["source_month"]:
                        raise ValueError("Candidate month differs from mining metadata")
                    observed_count += 1
                    yield candidate
                except (KeyError, ValueError, TypeError) as error:
                    raise ValueError(f"Invalid candidate {path}:{line_number}: {error}") from error
            if observed_count != receipt["counts"]["eligible_candidates"]:
                raise ValueError("Mining metadata candidate count differs from JSONL")


def run_build(arguments):
    if len(set(arguments.candidates)) != len(arguments.candidates):
        raise ValueError("Candidate inputs must not be repeated")
    receipts: list[dict] = []
    selected, selection_counts = select_candidates(load_candidates(arguments.candidates, receipts),
        max_puzzles=arguments.max_puzzles, max_per_motif=arguments.max_per_motif)
    source_months = {receipt["source_month"] for receipt in receipts}
    if len(source_months) != 1:
        raise ValueError("Candidate inputs must have one source month")
    source_month = next(iter(source_months))
    source_records = {json.dumps(receipt["source"], sort_keys=True) for receipt in receipts}
    filter_records = {json.dumps(receipt["filters"], sort_keys=True) for receipt in receipts}
    if len(source_records) != 1 or len(filter_records) != 1:
        raise ValueError("Candidate inputs must have identical provenance and filters")
    complete = all(receipt["completion"]["complete"] and receipt["source"]["sha256_verified"] for receipt in receipts)
    canonical_corpus = bool(re.fullmatch(r"lichess-standard-\d{4}-\d{2}-v[1-9]\d*", arguments.corpus_id))
    if canonical_corpus:
        if not complete or not arguments.corpus_id.startswith(f"lichess-standard-{source_month}-v"):
            raise ValueError("Canonical corpus requires complete checksum-verified input for its source month")
        source = receipts[0]["source"]
        if source["filename"] != f"lichess_db_standard_rated_{source_month}.pgn.zst" or source["sha256"] != source["expected_sha256"]:
            raise ValueError("Canonical source receipt is inconsistent")
    bundle = build_bundle(selected, arguments.corpus_id, arguments.title)
    bundle_bytes = json_bytes(bundle)
    counts = {name: sum(receipt["counts"][name] for receipt in receipts) for name in new_counts()}
    counts.update(selection_counts)
    manifest = {"format": "tempo-stalemate-swindle-manifest", "schema_version": 1,
        "corpus_id": arguments.corpus_id, "generator_version": 1, "source": receipts[0]["source"], "source_month": source_month,
        "complete_verified_source": complete, "filters": receipts[0]["filters"],
        "selection": {"max_puzzles": arguments.max_puzzles, "max_per_motif": arguments.max_per_motif},
        "counts": counts, "distribution_label": "Distribution among observed/selected stalemate examples",
        "count_scope": "terminal_stalemates counts header-eligible parsed draws; excluded header categories were not replayed",
        "distribution": selected_distribution(selected), "bundle_sha256": hashlib.sha256(bundle_bytes).hexdigest()}
    output, manifest_path = Path(arguments.output), Path(arguments.manifest)
    if output.resolve() == manifest_path.resolve():
        raise ValueError("Bundle and manifest must have distinct paths")
    if canonical_corpus and output.exists() and output.read_bytes() != bundle_bytes:
        raise ValueError("Canonical content already exists with different bytes; choose a new corpus revision")
    with staged_path(output) as staged_bundle, staged_path(manifest_path) as staged_manifest:
        staged_bundle.write_bytes(bundle_bytes)
        staged_manifest.write_bytes(json_bytes(manifest))
        publish_files([(staged_bundle, output), (staged_manifest, manifest_path)])
    print(json.dumps(counts, sort_keys=True), file=sys.stderr)


def parser() -> argparse.ArgumentParser:
    command_parser = argparse.ArgumentParser(description=__doc__)
    subcommands = command_parser.add_subparsers(dest="command", required=True)
    mine = subcommands.add_parser("mine", help="Stream decompressed PGN or a checksum-verified archive")
    mine.add_argument("--input", metavar="PATH|-", default=None)
    mine.add_argument("--output", required=True)
    mine.add_argument("--source-month", required=True)
    mine.add_argument("--source-url")
    mine.add_argument("--source-sha256")
    mine.add_argument("--min-material-deficit", type=int, default=5)
    mine.add_argument("--min-rating", type=int, default=1000)
    mine.add_argument("--speeds", default="blitz,rapid,classical")
    mine.add_argument("--include-bots", action="store_true")
    mine.add_argument("--max-games", type=positive_integer)
    mine.add_argument("--stop-after-candidates", type=positive_integer)
    mine.add_argument("--progress-every", type=positive_integer, default=1_000_000)
    mine.add_argument("--strict", action="store_true")
    mine.set_defaults(run=run_mine)
    build = subcommands.add_parser("build", help="Build a deterministic portable Study and manifest")
    build.add_argument("--candidates", nargs="+", required=True)
    build.add_argument("--corpus-id", required=True)
    build.add_argument("--title", default="Stalemate Swindles")
    build.add_argument("--max-puzzles", type=positive_integer, default=300)
    build.add_argument("--max-per-motif", type=positive_integer, default=40)
    build.add_argument("--output", required=True)
    build.add_argument("--manifest", required=True)
    build.set_defaults(run=run_build)
    return command_parser


def main() -> int:
    arguments = parser().parse_args()
    try:
        with cli_cancellation_signals() as cancellation:
            arguments.cancellation = cancellation
            arguments.run(arguments)
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"Stalemate Swindles: {error}", file=sys.stderr)
        return 1
    except (ArchiveCancellation, KeyboardInterrupt) as interruption:
        print("Stalemate Swindles: interrupted; no completed output published", file=sys.stderr)
        return 128 + (interruption.received_signal if isinstance(interruption, ArchiveCancellation) else signal.SIGINT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
