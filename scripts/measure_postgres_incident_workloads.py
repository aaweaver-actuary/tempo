"""Measure four incident workloads on an explicitly isolated PostgreSQL restore."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import sys
import time

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import postgres_store
from app.services import introduction_priorities, priority_retention, repertoire_opportunities
from app.threat_analysis_commands import claim_threat_analysis


def explain(database_url: str, statements: list[tuple[str, tuple]]) -> None:
    for statement, parameters in statements[:4]:
        if not statement.lstrip().upper().startswith(("SELECT", "UPDATE", "DELETE")):
            continue
        with psycopg.connect(database_url) as database:
            database.execute("SET statement_timeout='30s'")
            plan = database.execute(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, parameters,
            ).fetchone()[0][0]
            database.rollback()  # EXPLAIN ANALYZE of writes is safe only on this restore.
        root = plan["Plan"]
        print({"plan_node": root["Node Type"], "estimated_rows": root["Plan Rows"],
               "actual_rows": root["Actual Rows"], "execution_ms": plan["Execution Time"],
               "shared_hit_blocks": root.get("Shared Hit Blocks", 0),
               "shared_read_blocks": root.get("Shared Read Blocks", 0)}, flush=True)
        all_nodes = []
        def collect(node):
            all_nodes.append((node.get("Actual Total Time", 0), node["Node Type"],
                              node.get("Relation Name"), node.get("Plan Rows"),
                              node.get("Actual Rows"), node.get("Actual Loops")))
            for child in node.get("Plans", []):
                collect(child)
        collect(root)
        print({"slow_plan_nodes": sorted(all_nodes, reverse=True)[:6]}, flush=True)
        print({"scan_nodes": [node for node in all_nodes if node[2] is not None]}, flush=True)


@contextmanager
def capture_queries():
    captured: list[tuple[str, tuple]] = []
    original_native = postgres_store.PostgresConnection.execute_native
    original_translated = postgres_store.PostgresConnection.execute

    def native(database, statement, parameters=()):
        captured.append((statement, tuple(parameters)))
        return original_native(database, statement, parameters)

    def translated(database, statement, parameters=()):
        converted = postgres_store.postgres_sql(statement)
        if converted:
            captured.append((converted, tuple(parameters)))
        return original_translated(database, statement, parameters)

    postgres_store.PostgresConnection.execute_native = native
    postgres_store.PostgresConnection.execute = translated
    try:
        yield captured
    finally:
        postgres_store.PostgresConnection.execute_native = original_native
        postgres_store.PostgresConnection.execute = original_translated


def measure_section(label: str, operation, database_url: str, *, explain_plan: bool) -> bool:
    started = time.perf_counter()
    failed = False
    with capture_queries() as captured:
        try:
            result = operation()
        except Exception as error:
            failed = True
            result = f"FAILED {type(error).__name__}: {error}"
    seconds = time.perf_counter() - started
    print(f"{label}: whole_section_seconds={seconds:.3f} result={result}", flush=True)
    if explain_plan:
        try:
            explain(database_url, captured)
        except Exception as error:
            print(f"{label}: plan collection failed: {type(error).__name__}: {error}", flush=True)
    return failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--isolated-restore", action="store_true", required=True)
    parser.add_argument("--explain", action="store_true")
    options = parser.parse_args()
    if not options.isolated_restore:
        parser.error("This tool runs EXPLAIN ANALYZE and writes; use only an isolated restore")
    os.environ["TEMPO_DATABASE_WRITE_URL"] = options.dsn
    os.environ["TEMPO_DATABASE_READ_URL"] = options.dsn
    os.environ["TEMPO_POSTGRES_BACKGROUND_TRANSACTION_TIMEOUT_MS"] = "250"
    os.environ["TEMPO_POSTGRES_BACKGROUND_LOCK_TIMEOUT_MS"] = "25"

    with psycopg.connect(options.dsn) as database:
        version = database.execute("SELECT MAX(version) FROM tempo_schema_migrations").fetchone()[0]
        if version != 20:
            raise RuntimeError(f"Restore schema must be 20 for candidate measurements; found {version}")
        recurring = database.execute(
            "SELECT repertoire_id,card_id,COUNT(*) FROM repertoire_decision_events "
            "WHERE card_id IS NOT NULL GROUP BY repertoire_id,card_id "
            "ORDER BY COUNT(*) DESC LIMIT 1"
        ).fetchone()
        retention = database.execute(
            "SELECT repertoire_id,COUNT(*) FROM repertoire_card_priority_generations "
            "GROUP BY repertoire_id ORDER BY COUNT(*) DESC LIMIT 1"
        ).fetchone()
        popular = database.execute(
            "SELECT fen_key,COUNT(*) FROM game_position_occurrences "
            "WHERE move_uci IS NOT NULL GROUP BY fen_key ORDER BY COUNT(*) DESC LIMIT 8"
        ).fetchall()
        queued_threats = database.execute(
            "SELECT COUNT(*) FROM threat_analysis_requests WHERE state='queued'"
        ).fetchone()[0]
    print({"source": "verified backup restored into isolated PostgreSQL",
           "queued_threats": queued_threats,
           "recurring_events_for_top_card": recurring[2] if recurring else 0,
           "priority_rows_for_top_repertoire": retention[1] if retention else 0,
           "popular_position_counts": [row[1] for row in popular]}, flush=True)

    def threat_claim():
        with postgres_store.connection(background=True) as database:
            claimed = claim_threat_analysis(database, {})
            database.rollback()
            return bool(claimed["job"])

    failures = [measure_section("defensive_threat_claim", threat_claim, options.dsn,
                                explain_plan=options.explain)]

    if recurring:
        def recurring_read():
            with postgres_store.connection(read_only=True, background=True) as database:
                return len(repertoire_opportunities._load_recurring_decisions(
                    database, recurring[0], recurring[1]
                ))
        failures.append(measure_section("recurring_decision_evidence", recurring_read,
                                        options.dsn, explain_plan=options.explain))

    if retention:
        original_lock = priority_retention.lock_current_slice
        original_enqueue = priority_retention.enqueue_task_in_transaction
        priority_retention.lock_current_slice = lambda _database, _task: True
        priority_retention.enqueue_task_in_transaction = lambda *_args, **_kwargs: None
        try:
            task = {"id": "isolated-rehearsal", "generation": 1,
                    "lease_token": "isolated-rehearsal",
                    "payload": {"repertoire_id": retention[0]}}
            failures.append(measure_section("priority_retention_slice",
                            lambda: priority_retention.execute_priority_retention_slice(task),
                            options.dsn, explain_plan=options.explain))
        finally:
            priority_retention.lock_current_slice = original_lock
            priority_retention.enqueue_task_in_transaction = original_enqueue

    if popular:
        @contextmanager
        def read_section():
            with postgres_store.connection(read_only=True, background=True) as database:
                yield database

        failures.append(measure_section("position_occurrence_evidence",
                        lambda: len(introduction_priorities._read_personal_evidence_rows(
                            read_section, [row[0] for row in popular], "white"
                        )), options.dsn, explain_plan=options.explain))
    postgres_store.close_pools()
    if any(failures):
        raise SystemExit("One or more representative workloads exceeded the candidate budget")


if __name__ == "__main__":
    main()
