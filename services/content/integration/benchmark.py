"""Bounded fixture measurements on the owning runner's disposable database."""

import json
import os
from pathlib import Path
import random
import resource
import statistics
import subprocess
import sys
import time
import uuid

import chess
import psycopg
from tempo_content.store import ContentService
from tempo_content_publication_contracts.v1 import serialize_contract


def representative_source(workspace_id, *, route_count=512, exercise_count=2048, route_plies=64):
    if not 1 <= route_plies <= 64:
        raise ValueError("Representative route length must be between 1 and 64 plies")
    source_key = "benchmark-" + uuid.uuid4().hex
    routes = []
    for index in range(route_count):
        board = chess.Board()
        chooser = random.Random(index + 108)
        moves = []
        for _ in range(route_plies):
            legal = sorted(board.legal_moves, key=lambda move: move.uci())
            if not legal:
                break
            move = chooser.choice(legal)
            moves.append(move.uci())
            board.push(move)
        routes.append({"key": f"route-{index}", "starting_fen": chess.STARTING_FEN, "moves": moves})
    return {
        "schema_version": 1, "workspace_id": workspace_id, "source_key": source_key,
        "title": "Representative bounded Content benchmark",
        "repertoires": [{"key": "main", "title": "Main", "learner_color": "white"}],
        "policies": [{"key": "main", "repertoire_key": "main"}],
        "routes": routes,
        "memberships": [{"repertoire_key": "main", "policy_key": "main", "route_key": route["key"]} for route in routes],
        "exercises": [{"key": f"exercise-{index}", "decision_key": f"decision-{index}", "policy_key": "main",
                       "starting_fen": chess.STARTING_FEN, "prompt": "Explain the position",
                       "specification": {"type": "explanation", "rubric": "Describe a legal plan and its purpose"}}
                      for index in range(exercise_count)],
        "bindings": [],
    }


def summary(values):
    return {"count": len(values), "total_seconds": sum(values), "maximum_seconds": max(values, default=0),
            "median_seconds": statistics.median(values) if values else 0}


def run():
    if not os.environ.get("TEMPO_CONTENT_TEST_CONTAINER", "").startswith("tempo-content-"):
        raise RuntimeError("Benchmark requires the Content-owned disposable PostgreSQL runner")
    database_url = os.environ["TEMPO_CONTENT_DATABASE_URL"]
    workspace_id = os.environ["TEMPO_CONTENT_WORKSPACE"]
    startup_start = time.perf_counter()
    process = subprocess.run([sys.executable, "-I", "-c", "import tempo_content; print('imported')"],
                             capture_output=True, text=True, check=True, cwd="/private/tmp")
    assert process.stdout.strip() == "imported"
    cold_import = time.perf_counter() - startup_start
    source = representative_source(workspace_id)
    started = time.perf_counter()
    service = ContentService(database_url, workspace_id, pool_size=2)
    readiness = time.perf_counter() - started
    slices = []
    transaction_upper_bounds = []
    reported_metrics = {}
    try:
        started = time.perf_counter()
        loaded = service.load_source(source, expected_revision=0)
        registration = time.perf_counter() - started
        for _ in range(20000):
            state = service.inventory_status(source["source_key"])
            if state["state"] == "complete":
                break
            if state["state"] == "failed":
                raise AssertionError(state)
            lock_acquired = []

            def observe(event, context):
                if event == "after_source_lock":
                    lock_acquired.append(time.perf_counter())

            started = time.perf_counter()
            result = service.work_once(source["source_key"], hook=observe)
            finished = time.perf_counter()
            slices.append(finished - started)
            if lock_acquired:
                transaction_upper_bounds.append(finished - lock_acquired[-1])
            for key, value in result.items():
                if key.endswith("_seconds") and isinstance(value, (int, float)):
                    reported_metrics.setdefault(key, []).append(value)
        else:
            raise AssertionError("Representative fixture exceeded its declared slice budget")
        started = time.perf_counter()
        publication = service.publication_build(source["source_key"])
        publication_seconds = time.perf_counter() - started
        payload = serialize_contract(publication)
        recorded_transactions = list(service.transactions)
    finally:
        service.close()
    started = time.perf_counter()
    with ContentService(database_url, workspace_id, pool_size=2) as restarted:
        assert serialize_contract(restarted.publication_show(publication.publication_id, publication.publication_generation)) == payload
        replay = restarted.load_source(source, expected_revision=0)
        assert replay["replayed"]
    restart_replay = time.perf_counter() - started
    with psycopg.connect(database_url) as database:
        rows = {table: database.execute(f"SELECT count(*) FROM content.{table}").fetchone()[0]
                for table in ("sources", "artifacts", "occurrences", "positions", "legal_replies", "publications")}
        source_rows = {
            "route_memberships": database.execute("SELECT count(*) FROM content.generation_routes WHERE source_id=%s AND generation=%s", (loaded["source_id"], loaded["generation"])).fetchone()[0],
            "artifacts": database.execute("SELECT count(DISTINCT artifact_id) FROM content.generation_routes WHERE source_id=%s AND generation=%s", (loaded["source_id"], loaded["generation"])).fetchone()[0],
            "route_occurrences": database.execute("SELECT count(*) FROM content.occurrences o JOIN content.generation_routes r USING(artifact_id) WHERE r.source_id=%s AND r.generation=%s", (loaded["source_id"], loaded["generation"])).fetchone()[0],
            "canonical_route_positions": database.execute("SELECT count(DISTINCT o.position_key) FROM content.occurrences o JOIN content.generation_routes r USING(artifact_id) WHERE r.source_id=%s AND r.generation=%s", (loaded["source_id"], loaded["generation"])).fetchone()[0],
            "opponent_positions": database.execute("SELECT count(DISTINCT o.position_key) FROM content.occurrences o JOIN content.generation_routes r USING(artifact_id) WHERE r.source_id=%s AND r.generation=%s AND o.opponent", (loaded["source_id"], loaded["generation"])).fetchone()[0],
            "legal_replies": database.execute("SELECT count(*) FROM content.legal_replies WHERE position_key IN (SELECT o.position_key FROM content.occurrences o JOIN content.generation_routes r USING(artifact_id) WHERE r.source_id=%s AND r.generation=%s AND o.opponent)", (loaded["source_id"], loaded["generation"])).fetchone()[0],
        }
    memory = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "fixture": {"source_id": loaded["source_id"], "routes": len(source["routes"]),
                    "maximum_route_plies": max(len(route["moves"]) for route in source["routes"]),
                    "exercises": len(source["exercises"]), "serialized_source_bytes": len(json.dumps(source).encode())},
        "cold_installed_import_seconds": cold_import, "runtime_readiness_seconds": readiness,
        "source_registration_seconds": registration, "inventory_slices": summary(slices),
        "publication_seconds": publication_seconds, "restart_and_replay_seconds": restart_replay,
        "commit_transaction_upper_bounds": summary(transaction_upper_bounds),
        "reported_slice_metrics": {key: summary(values) for key, values in reported_metrics.items()},
        "recorded_writer_transactions": summary(recorded_transactions),
        "peak_process_rss_bytes": memory if sys.platform == "darwin" else memory * 1024,
        "fixture_row_counts": source_rows,
        "disposable_database_total_row_counts": rows,
        "limits": "One source, 512 routes of at most 64 plies and 2048 explanation exercises; no arbitrary scalability claim. RSS includes the benchmark driver. Commit upper bounds exclude read/lease transactions; recorded writer durations include pool acquisition and commit.",
    }


if __name__ == "__main__":
    result = run()
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    else:
        print(json.dumps(result, sort_keys=True))
