"""Compare frozen pre-change and current priority handlers on disposable PostgreSQL."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import platform
import sys
import threading
import time
import tracemalloc
import uuid

import chess
import psycopg
from psycopg import sql
from psycopg.errors import LockNotAvailable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
from scripts.apply_postgres_migrations import apply_migrations


ADMIN_DSN = "postgresql://postgres@postgres:5432/postgres"
REPERTOIRE_ID = "priority-benchmark"
FROZEN_TIME = datetime(2026, 10, 1, tzinfo=timezone.utc)
PAST = "2026-09-29T00:00:00+00:00"
BASELINE_BLOB = "648abb9b7cf2eb74decc51b3aaeb6a9fd6f21909"
BASELINE_REVISION = "86daf0053cdf7cf523956a723e5b309031ba09bd"
BASELINE_SOURCE = ROOT / "scripts" / "benchmarks" / "priority_baseline_postgres.py"


def import_baseline():
    raw = BASELINE_SOURCE.read_bytes()
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    assert blob == BASELINE_BLOB, f"Baseline source changed: {blob}"
    module_spec = spec_from_file_location("app.services.priority_baseline_postgres", BASELINE_SOURCE)
    assert module_spec and module_spec.loader
    module = module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def seed_input(database, card_count: int) -> None:
    database.execute(
        "INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,%s,%s,%s)",
        (REPERTOIRE_ID, "Priority benchmark", "benchmark.pgn", PAST),
    )
    first_route = ["d2d4", "d7d5", "g1f3", "g8f6", "c2c4"]
    second_route = ["d2d4", "g8f6", "g1f3", "d7d5", "c2c4"]
    transposed_board = chess.Board()
    for move_uci in first_route[:4]:
        transposed_board.push_uci(move_uci)
    card_routes = (
        (chess.STARTING_FEN, ["d2d4"]),
        (chess.STARTING_FEN, first_route[:3]),
        (chess.STARTING_FEN, second_route[:3]),
        (transposed_board.fen(), ["c2c4"]),
    )
    for ordinal, moves in enumerate((first_route, second_route)):
        database.execute(
            "INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,"
            "moves_json,created_at) VALUES(%s,%s,%s,'white',%s,%s,%s)",
            (f"line-{ordinal}", REPERTOIRE_ID, f"Line {ordinal}", chess.STARTING_FEN,
             json.dumps(moves), PAST),
        )
    for ordinal in range(card_count):
        start_fen, moves = card_routes[ordinal % len(card_routes)]
        card_id = f"card-{ordinal:04d}"
        database.execute(
            "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) "
            "VALUES(%s,%s,'prefix',%s,%s,'2026-09-29')",
            (card_id, REPERTOIRE_ID, start_fen, json.dumps(moves)),
        )
        database.execute(
            "INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)",
            (REPERTOIRE_ID, card_id),
        )
    database.execute(
        "INSERT INTO repertoire_coverage_runs(id,repertoire_id,status,settings_json,"
        "total_nodes,completed_nodes,created_at,updated_at) "
        "VALUES('run',%s,'complete','{}',1,1,%s,%s)",
        (REPERTOIRE_ID, PAST, PAST),
    )
    after_first_move = chess.Board()
    after_first_move.push_uci("d2d4")
    fen_key = " ".join(after_first_move.fen().split()[:4])
    database.execute(
        "INSERT INTO repertoire_coverage_nodes(id,run_id,repertoire_id,fen,fen_key,ply,"
        "trained_color,routes_json,covered_replies_json,explorer_status,maia_status,"
        "explorer_games,updated_at) VALUES('node','run',%s,%s,%s,1,'white','[]','[]',"
        "'complete','complete',100,%s)",
        (REPERTOIRE_ID, after_first_move.fen(), fen_key, PAST),
    )
    for move_uci, probability in (("d7d5", 0.7), ("g8f6", 0.3)):
        database.execute(
            "INSERT INTO repertoire_coverage_candidates(node_id,move_uci,"
            "explorer_probability,maia_probability,source_state) "
            "VALUES('node',%s,%s,%s,'blended')",
            (move_uci, probability, probability),
        )
    database.execute(
        "INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,"
        "start_fen,moves_json,analysis_version) VALUES('personal','lichess','benchmark',"
        "%s,'rapid',1,'white','1-0',%s,'[]',1)",
        ("2026-09-01T00:00:00+00:00", chess.STARTING_FEN),
    )
    database.execute(
        "INSERT INTO game_position_occurrences_legacy(game_id,ply,fen_key,move_uci) "
        "VALUES('personal',1,%s,'d7d5')", (fen_key,),
    )
    database.execute(
        "INSERT INTO repertoires(id,name,source_name,created_at) "
        "VALUES('foreground-review','Foreground review','review.pgn',%s)", (PAST,),
    )
    database.execute(
        "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) "
        "VALUES('foreground-card','foreground-review','prefix',%s,'[]','2026-09-29')",
        (chess.STARTING_FEN,),
    )


def reset_derived(database) -> None:
    database.execute("DELETE FROM background_tasks WHERE deduplication_key=%s", (REPERTOIRE_ID,))
    database.execute("DELETE FROM repertoire_priority_publications WHERE repertoire_id=%s", (REPERTOIRE_ID,))
    database.execute("DELETE FROM repertoire_card_priority_generations WHERE repertoire_id=%s", (REPERTOIRE_ID,))
    database.execute("DELETE FROM repertoire_priority_preparations WHERE repertoire_id=%s", (REPERTOIRE_ID,))
    database.execute("DELETE FROM repertoire_priority_jobs WHERE repertoire_id=%s", (REPERTOIRE_ID,))
    database.execute(
        "INSERT INTO repertoire_priority_jobs(repertoire_id,generation,status,next_attempt_at,updated_at) "
        "VALUES(%s,1,'queued',%s,%s)", (REPERTOIRE_ID, PAST, PAST),
    )
    database.execute(
        "INSERT INTO background_tasks(id,kind,deduplication_key,generation,priority,state,"
        "phase,payload_json,next_attempt_at,created_at,updated_at) "
        "VALUES(%s,'repertoire_priority',%s,1,131,'queued','queued',%s,%s,%s,%s)",
        (str(uuid.uuid4()), REPERTOIRE_ID,
         json.dumps({"repertoire_id": REPERTOIRE_ID, "generation": 1}),
         PAST, PAST, PAST),
    )


def foreground_review_samples(database_module, sample_count: int) -> list[float]:
    samples = []
    for ordinal in range(sample_count):
        started = time.perf_counter()
        with database_module.connection(background=False) as database:
            database.execute(
                "INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,"
                "next_interval,source_kind) VALUES('foreground-card','good',?,1,2,'study')",
                (FROZEN_TIME.isoformat(),),
            )
        samples.append((time.perf_counter() - started) * 1000)
    return samples


def measure_one(mode: str, card_count: int, baseline, current, database_url: str) -> dict:
    from app import database as database_module, postgres_store
    from app.services import durable_tasks, introduction_priorities

    with psycopg.connect(database_url) as observer:
        reset_derived(observer)
        observer.commit()

    module = baseline if mode == "baseline" else current
    original_loader = module._load_priority_calculation_input
    original_calculator = module.calculate_priority_records
    original_connection = module.connection
    original_datetime = module.datetime
    original_execute = postgres_store.PostgresConnection.execute
    input_depth = threading.local()
    main_thread = threading.current_thread()
    measurements = {
        "input_loader_calls": 0, "calculator_calls": 0, "sql_input_reads": 0,
        "input_loader_seconds": 0.0, "calculator_seconds": 0.0,
        "transaction_ms": [], "lock_statement_ms": [], "lock_timeouts": 0,
    }

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return FROZEN_TIME if tz is None else FROZEN_TIME.astimezone(tz)

    def measured_loader(repertoire_id, *, background, calculated_at=None):
        measurements["input_loader_calls"] += 1
        started = time.perf_counter()
        input_depth.active = True
        try:
            return original_loader(repertoire_id, background=background,
                                   calculated_at=FROZEN_TIME)
        finally:
            input_depth.active = False
            measurements["input_loader_seconds"] += time.perf_counter() - started

    def measured_calculator(calculation_input):
        measurements["calculator_calls"] += 1
        started = time.perf_counter()
        try:
            return original_calculator(calculation_input)
        finally:
            measurements["calculator_seconds"] += time.perf_counter() - started

    @contextmanager
    def measured_connection(*, background):
        started = time.perf_counter()
        try:
            with original_connection(background=background) as database:
                yield database
        finally:
            measurements["transaction_ms"].append((time.perf_counter() - started) * 1000)

    def measured_execute(database, statement, parameters=()):
        started = time.perf_counter()
        try:
            return original_execute(database, statement, parameters)
        except LockNotAvailable:
            measurements["lock_timeouts"] += 1
            raise
        finally:
            if getattr(input_depth, "active", False) and statement.lstrip().upper().startswith("SELECT"):
                measurements["sql_input_reads"] += 1
            if threading.current_thread() is main_thread and "FOR UPDATE" in statement.upper():
                measurements["lock_statement_ms"].append(
                    (time.perf_counter() - started) * 1000)

    module._load_priority_calculation_input = measured_loader
    module.calculate_priority_records = measured_calculator
    module.connection = measured_connection
    module.datetime = FixedDatetime
    postgres_store.PostgresConnection.execute = measured_execute
    tracemalloc.start()
    progress = []
    phase_seconds = {"preparation": 0.0, "staging": 0.0, "publication": 0.0}
    claim_handler_seconds = 0.0
    claims = 0
    review_latencies = []
    started = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=1) as review_pool:
            review_future = review_pool.submit(foreground_review_samples, database_module, 8)
            with psycopg.connect(database_url) as observer:
                for attempt in range(card_count + 4):
                    handler_started = time.perf_counter()
                    claimed = durable_tasks.claim_task(kind="repertoire_priority")
                    assert claimed is not None, f"No claim at attempt {attempt}; progress={progress}"
                    payload_cursor = claimed["payload"].get("cursor")
                    assert module.execute_repertoire_priority_slice(claimed)
                    elapsed = time.perf_counter() - handler_started
                    claim_handler_seconds += elapsed
                    claims += 1
                    if mode == "current" and payload_cursor is None:
                        phase_seconds["preparation"] += elapsed
                    elif payload_cursor is not None and int(payload_cursor) >= card_count:
                        phase_seconds["publication"] += elapsed
                    else:
                        phase_seconds["staging"] += elapsed
                    if claims == 1 or claims % 16 == 0 or claims == card_count + 1:
                        staged_count = observer.execute(
                            "SELECT COUNT(*) FROM repertoire_card_priority_generations "
                            "WHERE repertoire_id=%s AND generation=1", (REPERTOIRE_ID,),
                        ).fetchone()[0]
                        prepared_count = observer.execute(
                            "SELECT COUNT(*) FROM repertoire_priority_prepared_rows "
                            "WHERE repertoire_id=%s AND generation=1", (REPERTOIRE_ID,),
                        ).fetchone()[0]
                        progress.append({"handler_calls": claims, "prepared": prepared_count,
                                         "staged": staged_count})
                        observer.commit()
                    publication = observer.execute(
                        "SELECT generation FROM repertoire_priority_publications WHERE repertoire_id=%s",
                        (REPERTOIRE_ID,),
                    ).fetchone()
                    observer.commit()
                    if publication == (1,):
                        progress.append({"handler_calls": claims,
                                         "prepared": observer.execute(
                                             "SELECT COUNT(*) FROM repertoire_priority_prepared_rows "
                                             "WHERE repertoire_id=%s AND generation=1",
                                             (REPERTOIRE_ID,),
                                         ).fetchone()[0],
                                         "staged": observer.execute(
                                             "SELECT COUNT(*) FROM repertoire_card_priority_generations "
                                             "WHERE repertoire_id=%s AND generation=1",
                                             (REPERTOIRE_ID,),
                                         ).fetchone()[0]})
                        observer.commit()
                        break
                else:
                    raise AssertionError(f"Publication exceeded {card_count + 3} claims: {progress}")
            review_latencies = review_future.result(timeout=30)
        wall_elapsed = time.perf_counter() - started
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        module._load_priority_calculation_input = original_loader
        module.calculate_priority_records = original_calculator
        module.connection = original_connection
        module.datetime = original_datetime
        postgres_store.PostgresConnection.execute = original_execute

    with psycopg.connect(database_url) as observer:
        prepared_rows = observer.execute(
            "SELECT COUNT(*) FROM repertoire_priority_prepared_rows "
            "WHERE repertoire_id=%s AND generation=1", (REPERTOIRE_ID,),
        ).fetchone()[0]
        published_rows = observer.execute(
            "SELECT card_id,priority_score,evidence_json,completed_line_ids_json,"
            "frontier_decisions_json FROM repertoire_card_priority_generations "
            "WHERE repertoire_id=%s AND generation=1 ORDER BY card_id", (REPERTOIRE_ID,),
        ).fetchall()
    assert len(published_rows) == card_count, (mode, card_count, len(published_rows))
    assert measurements["lock_timeouts"] == 0
    return {
        "mode": mode, "cards": card_count, "repertoire_lines": 2,
        "prepared_rows": prepared_rows, "staged_rows": len(published_rows),
        "committed_progress": progress, "handler_claims": claims,
        "preparation_seconds": round(phase_seconds["preparation"], 6),
        "staging_seconds": round(phase_seconds["staging"], 6),
        "publication_seconds": round(phase_seconds["publication"], 6),
        "claim_handler_seconds": round(claim_handler_seconds, 6),
        "wall_elapsed_seconds": round(wall_elapsed, 6),
        "peak_traced_python_bytes": peak_bytes,
        "input_loader_calls": measurements["input_loader_calls"],
        "calculator_calls": measurements["calculator_calls"],
        "sql_input_reads": measurements["sql_input_reads"],
        "input_loader_seconds": round(measurements["input_loader_seconds"], 6),
        "calculator_seconds": round(measurements["calculator_seconds"], 6),
        "transaction_count": len(measurements["transaction_ms"]),
        "transaction_max_ms": round(max(measurements["transaction_ms"], default=0), 3),
        "transaction_total_ms": round(sum(measurements["transaction_ms"]), 3),
        "lock_statement_count": len(measurements["lock_statement_ms"]),
        "lock_statement_max_ms": round(max(measurements["lock_statement_ms"], default=0), 3),
        "lock_statement_total_ms": round(sum(measurements["lock_statement_ms"]), 3),
        "lock_timeouts": measurements["lock_timeouts"],
        "foreground_review_insert_samples_ms": [round(value, 3) for value in review_latencies],
        "published_rows_sha256": hashlib.sha256(
            json.dumps(published_rows, sort_keys=True, default=str).encode()).hexdigest(),
        "failures": 0, "retries": 0,
    }


def main() -> None:
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("Priority benchmark requires a disposable PostgreSQL instance")
    database_name = f"tempo_priority_benchmark_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as administrator:
        administrator.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    benchmark_dsn = f"postgresql://postgres@postgres:5432/{database_name}"
    try:
        apply_migrations(benchmark_dsn)
        with psycopg.connect(benchmark_dsn) as database:
            database.execute("INSERT INTO settings(id) VALUES(1)")
            seed_input(database, 64)
            database.commit()
        os.environ["TEMPO_DATABASE_WRITE_URL"] = benchmark_dsn
        os.environ["TEMPO_DATABASE_READ_URL"] = benchmark_dsn
        baseline = import_baseline()
        from app.services import postgres_priority
        observations = []
        for mode in ("baseline", "current", "current", "baseline"):
            observations.append(measure_one(mode, 64, baseline, postgres_priority, benchmark_dsn))
        with psycopg.connect(benchmark_dsn) as database:
            database.execute("DELETE FROM reviews WHERE card_id='foreground-card'")
            database.execute("DELETE FROM background_tasks WHERE deduplication_key=%s", (REPERTOIRE_ID,))
            database.execute("DELETE FROM repertoire_priority_publications WHERE repertoire_id=%s", (REPERTOIRE_ID,))
            database.execute("DELETE FROM repertoire_card_priority_generations WHERE repertoire_id=%s", (REPERTOIRE_ID,))
            database.execute("DELETE FROM repertoire_priority_preparations WHERE repertoire_id=%s", (REPERTOIRE_ID,))
            database.execute("DELETE FROM repertoire_priority_jobs WHERE repertoire_id=%s", (REPERTOIRE_ID,))
            database.execute("DELETE FROM repertoires WHERE id=%s", (REPERTOIRE_ID,))
            database.execute("DELETE FROM imported_games WHERE id='personal'")
            database.execute("DELETE FROM repertoires WHERE id='foreground-review'")
            database.commit()
            seed_input(database, 1000)
            database.commit()
        observations.append(measure_one("current", 1000, baseline, postgres_priority, benchmark_dsn))
        assert all(observation["published_rows_sha256"] == observations[0]["published_rows_sha256"]
                   for observation in observations[:4]), "Comparable published evidence changed"
        artifact = {
            "baseline_revision": BASELINE_REVISION,
            "baseline_source_blob": BASELINE_BLOB,
            "corrected_head": os.getenv("TEMPO_PRIORITY_BENCHMARK_HEAD"),
            "corrected_head_dirty": os.getenv("TEMPO_PRIORITY_BENCHMARK_DIRTY") == "true",
            "frozen_calculation_time": FROZEN_TIME.isoformat(),
            "fixture": "Two transposing move orders; shared and transposed cards repeated across 64 or 1000 cards; one completed coverage node, two reply candidates, one personal game position",
            "cache_state": "One schema/image build, alternating 64-card observations after fixture creation; PostgreSQL and Python caches retained across repetitions; 1000-card corrected fixture freshly seeded",
            "hardware": {"platform": platform.platform(), "machine": platform.machine(),
                         "processor": platform.processor(), "cpu_count_visible": os.cpu_count(),
                         "python": platform.python_version(),
                         "memory_limit_cgroup": Path("/sys/fs/cgroup/memory.max").read_text().strip()
                         if Path("/sys/fs/cgroup/memory.max").exists() else None},
            "limits": {"background_transaction_ms": 250, "background_lock_ms": 25,
                       "maximum_claims": "cards+4", "peak_memory_method": "tracemalloc peak Python allocations across claim/handler and concurrent foreground review inserts"},
            "observations": observations,
            "limitations": ["Foreground samples measure review-row commits, not the HTTP review workflow",
                            "Lock statement durations include execution and possible waiting; they do not isolate wait time",
                            "tracemalloc omits native allocator and PostgreSQL server memory"],
        }
        print("PRIORITY_BENCHMARK_JSON=" + json.dumps(artifact, separators=(",", ":")), flush=True)
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as administrator:
            administrator.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                sql.Identifier(database_name)
            ))


if __name__ == "__main__":
    main()
