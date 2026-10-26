"""Rehearse graph cleanup only on the runner-owned PostgreSQL stack.

Run ``--mode baseline`` before changing the handlers to capture their actual SQL
and complete transaction timings. The default mode also checks durable outcomes.
The disposable environment, database marker and current schema must pass a
read-only guard before writes. Every workload uses its own new helper database.
"""

from __future__ import annotations

# Preserve direct-script imports after relocation from scripts/.
import sys
from pathlib import Path
_REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPOSITORY))
sys.path.insert(0, str(_REPOSITORY / "database"))
sys.path.insert(0, str(_REPOSITORY / "backend"))

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import time
from unittest.mock import patch
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.errors import TransactionTimeout
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from tempo_legacy_database.adapters import postgres_store
from app.services import postgres_opening_graph as graph, redis_admission_gate
from database.tooling.postgres_rehearsal_admission import admitted_call


DATABASE_URL = "postgresql://postgres@postgres:5432/tempo"
DISPOSABLE_DATABASE_MARKER = "tempo-disposable-postgres-test"
FIXTURE_SIZE = 64_000
SETUP_BATCH_SIZE = 1_000
GRAPH_GENERATION = 7
OBSOLETE_ORDINALS = (0, 1, 2, 32_000, 63_483, 63_484, 63_485)
NON_OPENING_START = 63_488
FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


class IncidentFixture:
    def __init__(self) -> None:
        self.prefix = "graph-retention-" + uuid.uuid4().hex
        self.repertoire_id = self.prefix + "-main"
        self.other_repertoire_id = self.prefix + "-other"
        self.task_ids: list[str] = []
        self.report: dict = {"fixture_version": 1, "fixture_size": FIXTURE_SIZE,
                             "measurements": [], "plans": [], "regressions": [],
                             "setup_stages": [], "timing_notes":
                             "Connection context timings include acquisition, settings, SQL translation, "
                             "handler statements, metric flush, commit/rollback and pool return. EXPLAIN "
                             "runs separately against post-commit or post-rollback state after measured handlers; "
                             "its elapsed time is not a full transaction. Diagnostic conditional DELETE plans "
                             "run in a separate transaction that is rolled back."}

    def card_id(self, ordinal: int) -> str:
        return f"{self.prefix}-card-{ordinal:05d}"

    def seed(self) -> None:
        started_at = time.perf_counter()
        now = datetime.now(timezone.utc).isoformat()
        with psycopg.connect(DATABASE_URL) as database:
            stage_started_at = time.perf_counter()
            for repertoire_id in (self.repertoire_id, self.other_repertoire_id):
                database.execute(
                    "INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,%s,%s,%s)",
                    (repertoire_id, "Disposable incident rehearsal", "synthetic.pgn", now),
                )
                database.execute(
                    "INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,"
                    "start_fen,moves_json,created_at) VALUES(%s,%s,'Synthetic','white',%s,'[]',%s)",
                    (repertoire_id + "-line", repertoire_id, FEN, now),
                )
            self.report["setup_stages"].append({"label": "repertoires_lines", "seconds": time.perf_counter() - stage_started_at})
            stage_started_at = time.perf_counter()
            for first_ordinal in range(0, FIXTURE_SIZE, SETUP_BATCH_SIZE):
                last_ordinal = min(first_ordinal + SETUP_BATCH_SIZE, FIXTURE_SIZE) - 1
                database.execute(
                    "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type,canonical_route_source) "
                    "SELECT %s||'-card-'||lpad(number::text,5,'0'),%s,'prefix',%s,'[]',%s,"
                    "CASE WHEN number>=%s THEN 'defensive' ELSE 'opening' END,0 "
                    "FROM generate_series(%s::integer,%s::integer) number",
                    (self.prefix, self.repertoire_id, FEN, now[:10], NON_OPENING_START,
                     first_ordinal, last_ordinal),
                )
                database.commit()
            self.report["setup_stages"].append({"label": "cards_with_source_triggers", "seconds": time.perf_counter() - stage_started_at})
            stage_started_at = time.perf_counter()
            for first_ordinal in range(0, FIXTURE_SIZE, SETUP_BATCH_SIZE):
                last_ordinal = min(first_ordinal + SETUP_BATCH_SIZE, FIXTURE_SIZE) - 1
                database.execute(
                    "INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) "
                    "SELECT %s,id,0 FROM cards WHERE repertoire_id=%s AND id>=%s AND id<=%s ORDER BY id",
                    (self.repertoire_id, self.repertoire_id, self.card_id(first_ordinal), self.card_id(last_ordinal)),
                )
                database.commit()
            database.execute(
                "INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)",
                (self.other_repertoire_id, self.card_id(32_000)),
            )
            self.report["setup_stages"].append({"label": "links_with_source_triggers", "seconds": time.perf_counter() - stage_started_at})
            stage_started_at = time.perf_counter()
            # Two graph generations and a second repertoire stop an accidental
            # membership match from hiding missing repertoire/generation filters.
            for repertoire_id, generation in ((self.repertoire_id, GRAPH_GENERATION - 1),
                                               (self.repertoire_id, GRAPH_GENERATION),
                                               (self.other_repertoire_id, GRAPH_GENERATION)):
                database.execute(
                    "INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,"
                    "card_id,decision_fen_key,starting_fen,moves_json,trained_color) "
                    "SELECT %s,%s,%s,number,%s||'-card-'||lpad(number::text,5,'0'),"
                    "'synthetic',%s,'[]','white' FROM generate_series(0,%s) number "
                    "WHERE (%s<>%s OR number<>ALL(%s)) AND (%s<>%s OR number<%s)",
                    (repertoire_id, generation, repertoire_id + "-line", self.prefix, FEN,
                     FIXTURE_SIZE - 1 if repertoire_id == self.repertoire_id else 255,
                     generation, GRAPH_GENERATION, list(OBSOLETE_ORDINALS),
                     generation, GRAPH_GENERATION, NON_OPENING_START),
                )
            # One authored membership deliberately has no current graph step.
            # Raw-page cleanup must checkpoint past it without deriving deletion.
            database.execute("UPDATE cards SET canonical_route_source=1 WHERE id=%s", (self.card_id(3),))
            database.execute("UPDATE repertoire_cards SET canonical_route_source=1 "
                             "WHERE repertoire_id=%s AND card_id=%s", (self.repertoire_id, self.card_id(3)))
            database.execute("DELETE FROM opening_graph_steps WHERE repertoire_id=%s AND generation=%s AND card_id=%s",
                             (self.repertoire_id, GRAPH_GENERATION, self.card_id(3)))
            self.report["setup_stages"].append({"label": "graph_generations", "seconds": time.perf_counter() - stage_started_at})
            stage_started_at = time.perf_counter()
            database.execute(
                "INSERT INTO opening_graph_publications(repertoire_id,generation,published_at) "
                "VALUES(%s,%s,%s)", (self.repertoire_id, GRAPH_GENERATION, now),
            )
            database.execute(
                "INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) "
                "VALUES(%s,'good',%s,1,2)", (self.card_id(32_000), now),
            )
            database.execute(
                "INSERT INTO daily_queue(queue_date,card_id,position,status) "
                "VALUES(%s,%s,0,'queued')", (now[:10], self.card_id(0)),
            )
            self.report["setup_stages"].append({"label": "graph_history", "seconds": time.perf_counter() - stage_started_at})
            for table in ("cards", "repertoire_cards", "opening_graph_steps"):
                database.execute("ANALYZE " + table)
        self.report["setup_seconds"] = time.perf_counter() - started_at

    def task(self, kind: str, *, cursor: str = "", max_attempts: int = 5) -> dict:
        task_id = self.prefix + "-" + kind
        if task_id not in self.task_ids:
            self.task_ids.append(task_id)
        timestamp = datetime.now(timezone.utc)
        now = timestamp.isoformat()
        lease_token = uuid.uuid4().hex
        lease_expires_at = (timestamp + timedelta(minutes=1)).isoformat()
        payload = {"repertoire_id": self.repertoire_id, "local_day": now[:10], "after_card_id": cursor}
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as database:
            row = database.execute(
                "INSERT INTO background_tasks(id,kind,deduplication_key,generation,priority,state,phase,"
                "payload_json,attempt_count,max_attempts,next_attempt_at,lease_token,lease_expires_at,"
                "created_at,updated_at) VALUES(%s,%s,%s,%s,1,'leased',%s,%s,1,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(id) DO UPDATE SET generation=excluded.generation,state='leased',"
                "phase=excluded.phase,payload_json=excluded.payload_json,attempt_count=1,"
                "max_attempts=excluded.max_attempts,lease_token=excluded.lease_token,"
                "lease_expires_at=excluded.lease_expires_at,last_error=NULL,completed_at=NULL,"
                "next_attempt_at=excluded.next_attempt_at RETURNING *",
                (task_id, kind, self.repertoire_id, GRAPH_GENERATION if kind == "opening_graph_rebuild" else 1,
                 "cleanup" if kind == "opening_graph_rebuild" else "queued", json.dumps(payload),
                 max_attempts, now, lease_token, lease_expires_at, now, now),
            ).fetchone()
            # Even if a scheduler is accidentally left running, fixture task
            # creation and initial lease are atomic and its checkpoints paused.
            database.execute("INSERT INTO background_activity(source,work_id,paused,updated_at) "
                             "VALUES('durable',%s,1,%s) ON CONFLICT(source,work_id) "
                             "DO UPDATE SET paused=1,updated_at=excluded.updated_at", (task_id, now))
        assert row is not None
        row["payload"] = json.loads(row.pop("payload_json"))
        return row

    def claim(self, task_id: str, *, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as database:
            row = database.execute(
                "UPDATE background_tasks SET state='leased',attempt_count=attempt_count+1,"
                "lease_token=%s,lease_expires_at=%s,updated_at=%s "
                "WHERE id=%s AND state IN ('queued','retrying') AND next_attempt_at<=%s RETURNING *",
                (uuid.uuid4().hex, (now + timedelta(minutes=1)).isoformat(), now.isoformat(),
                 task_id, now.isoformat()),
            ).fetchone()
        assert row is not None, "Expected fixture-owned queued task: " + task_id
        row["payload"] = json.loads(row.pop("payload_json"))
        return row

    def state(self, task_id: str) -> dict:
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as database:
            row = database.execute("SELECT * FROM background_tasks WHERE id=%s", (task_id,)).fetchone()
        assert row is not None
        row["payload"] = json.loads(row.pop("payload_json"))
        return row

    def claim_retry_when_due(self, task_id: str) -> dict:
        """Use the real queue clock/eligibility path without wall-clock waiting."""
        from app.services import durable_tasks

        checkpoint = self.state(task_id)
        assert checkpoint["state"] == "retrying"
        due_at = datetime.fromisoformat(checkpoint["next_attempt_at"])
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as database:
            unowned_tasks = database.execute("SELECT id FROM background_tasks WHERE id<>ALL(%s)",
                                            (self.task_ids,)).fetchall()
            assert unowned_tasks == [], "Real retry claim must use the exclusively fixture-owned database"
            database.execute("UPDATE background_activity SET paused=0 WHERE source='durable' AND work_id=%s",
                             (task_id,))
        try:
            with patch.object(durable_tasks, "_now", return_value=due_at - timedelta(microseconds=1)):
                assert admitted_call(lambda: durable_tasks.claim_task(kind=checkpoint["kind"])) is None, "Retry claimed before its deadline"
            with patch.object(durable_tasks, "_now", return_value=due_at + timedelta(microseconds=1)):
                claimed = admitted_call(lambda: durable_tasks.claim_task(kind=checkpoint["kind"]))
            assert claimed is not None and claimed["id"] == task_id, claimed
            assert claimed["payload"] == checkpoint["payload"]
            return claimed
        finally:
            with psycopg.connect(DATABASE_URL) as database:
                database.execute("UPDATE background_activity SET paused=1 WHERE source='durable' AND work_id=%s",
                                 (task_id,))

    @contextmanager
    def capture(self, label: str):
        measurement = {"label": label, "transactions": [], "statements": []}
        original_connection = postgres_store.connection
        original_execute = postgres_store.PostgresConnection.execute_native
        original_compat_execute = postgres_store.PostgresConnection.execute

        @contextmanager
        def timed_connection(**options):
            started_at = time.perf_counter()
            outcome = "committed"
            settings = None
            try:
                with original_connection(**options) as database:
                    if options.get("background"):
                        settings = tuple(database.raw.execute(
                            "SELECT current_setting('transaction_timeout'),current_setting('lock_timeout')",
                        ).fetchone())
                        expected = (os.environ["TEMPO_POSTGRES_BACKGROUND_TRANSACTION_TIMEOUT_MS"] + "ms", "25ms")
                        assert settings == expected, ("Unexpected actual PostgreSQL background budget", settings, expected)
                    yield database
            except TransactionTimeout:
                outcome = "transaction_timeout"
                raise
            except Exception:
                outcome = "rolled_back"
                raise
            finally:
                measurement["transactions"].append({"background": bool(options.get("background")),
                    "read_only": bool(options.get("read_only")), "outcome": outcome,
                    "effective_settings": settings,
                    "seconds": time.perf_counter() - started_at})

        def record_statement(database, statement, parameters=()):
            upper_statement = statement.lstrip().upper()
            selected_read = upper_statement.startswith(("SELECT", "WITH")) and (
                ("repertoire_cards" in statement and "opening_graph_steps" in statement)
                or "repertoire_priority_prepared_rows" in statement
                or "repertoire_priority_preparations" in statement
                or "repertoire_card_priority_generations" in statement
            )
            conditional_manifest_delete = upper_statement.startswith("DELETE") and (
                "repertoire_priority_preparations" in statement
                and "repertoire_priority_prepared_rows" in statement
                and "NOT EXISTS" in upper_statement
            )
            if selected_read or conditional_manifest_delete:
                if not any(captured_statement == statement
                           for captured_statement, _ in measurement["statements"]):
                    measurement["statements"].append((statement, tuple(parameters)))

        def native_execute(database, statement, parameters=()):
            record_statement(database, statement, parameters)
            return original_execute(database, statement, parameters)

        def compat_execute(database, statement, parameters=()):
            translated_statement = postgres_store.postgres_sql(statement)
            if translated_statement is not None:
                record_statement(database, translated_statement, parameters)
            return original_compat_execute(database, statement, parameters)

        started_at = time.perf_counter()
        try:
            with patch.object(postgres_store, "connection", timed_connection), \
                    patch.object(postgres_store.PostgresConnection, "execute_native", native_execute), \
                    patch.object(postgres_store.PostgresConnection, "execute", compat_execute):
                yield measurement
        finally:
            measurement["total_seconds"] = time.perf_counter() - started_at
            self.report["measurements"].append({key: value for key, value in measurement.items()
                                                if key != "statements"})
            # Explain the actual handler statements after timing, with no budget
            # override in measured handlers. Row locks are released by rollback.
            with psycopg.connect(DATABASE_URL) as database:
                for statement, parameters in measurement["statements"]:
                    plan = database.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement,
                                            parameters).fetchone()[0]
                    self.report["plans"].append({"label": label, "sql": statement, "plan": plan})
                database.rollback()

    def measure_baseline(self) -> None:
        for label, ordinal in (("graph_mostly_current", 2), ("graph_nonmatching_tail", 63_485),
                               ("graph_raw_exhausted", FIXTURE_SIZE - 1)):
            with self.capture(label):
                try:
                    admitted_call(graph.prepare_obsolete_graph_cards, self.repertoire_id, GRAPH_GENERATION,
                                                       self.card_id(ordinal))
                except TransactionTimeout:
                    # A baseline timeout is evidence, not a rehearsal failure.
                    pass
        with self.capture("graph_cleanup_full_handler"):
            try:
                self.invoke_worker_slice(self.task("opening_graph_rebuild"))
            except TransactionTimeout:
                pass

    @staticmethod
    def invoke_worker_slice(task: dict) -> bool:
        from app import tasks

        with patch.object(tasks.celery_app, "send_task"):
            return tasks.execute_background_slice.run(task)

    def history(self) -> list[dict]:
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as database:
            return database.execute(
                "SELECT review.* FROM reviews review JOIN cards card ON card.id=review.card_id "
                "WHERE card.id=ANY(%s) ORDER BY review.id",
                ([self.card_id(ordinal) for ordinal in OBSOLETE_ORDINALS],),
            ).fetchall()

    def link_state(self, card_id: str) -> dict:
        with psycopg.connect(DATABASE_URL, row_factory=dict_row) as database:
            card = database.execute("SELECT id,repertoire_id,archived FROM cards WHERE id=%s",
                                    (card_id,)).fetchone()
            links = database.execute("SELECT repertoire_id FROM repertoire_cards WHERE card_id=%s "
                                     "ORDER BY repertoire_id", (card_id,)).fetchall()
            queue = database.execute("SELECT id,status FROM daily_queue WHERE card_id=%s ORDER BY id",
                                     (card_id,)).fetchall()
        return {"card": card, "links": links, "queue": queue}


    def test_postgres_graph_cleanup_current_pages_restart_and_shared_tail(self) -> None:
        history_before = self.history()
        task = self.task("opening_graph_rebuild")
        prepared = admitted_call(graph.prepare_obsolete_graph_cards, self.repertoire_id, GRAPH_GENERATION, "")
        assert prepared.obsolete_card_ids == (self.card_id(0), self.card_id(1)), prepared
        assert prepared.checkpoint_card_id == self.card_id(1), prepared
        with self.capture("graph_cleanup_full_drain"):
            assert self.invoke_worker_slice(task)
            checkpoint = self.state(task["id"])
            assert checkpoint["phase"] == "cleanup"
            assert checkpoint["payload"]["after_card_id"] == self.card_id(1)
            assert self.link_state(self.card_id(2))["links"], "Third obsolete card skipped by frontier"
            postgres_store.close_pools()
            # Restart from durable state, not the caller's prepared page.
            task = self.claim(task["id"])
            assert admitted_call(graph.execute_graph_cleanup_slice, task)
            checkpoint = self.state(task["id"])
            assert not self.link_state(self.card_id(2))["links"]
            assert checkpoint["payload"]["after_card_id"] == self.card_id(257)
            task = self.claim(task["id"])
            assert admitted_call(graph.execute_graph_cleanup_slice, task)
            checkpoint = self.state(task["id"])
            assert checkpoint["payload"]["after_card_id"] == self.card_id(513)
            assert checkpoint["phase"] == "cleanup", "Current-only page falsely finalized"
            for _ in range(FIXTURE_SIZE // 256 + 16):
                if checkpoint["phase"] == "finalize":
                    break
                task = self.claim(task["id"])
                assert admitted_call(graph.execute_graph_cleanup_slice, task)
                checkpoint = self.state(task["id"])
            else:
                raise AssertionError("Graph cleanup failed to drain bounded pages")
        assert checkpoint["phase"] == "finalize"
        for ordinal in OBSOLETE_ORDINALS:
            state = self.link_state(self.card_id(ordinal))
            if ordinal == 32_000:
                assert state["links"] == [{"repertoire_id": self.other_repertoire_id}], state
                assert state["card"]["archived"] == 0, state
                assert state["card"]["repertoire_id"] == self.other_repertoire_id, state
            else:
                assert state["links"] == [] and state["card"]["archived"] == 1, state
        assert self.link_state(self.card_id(0))["queue"][0]["status"] == "superseded"
        authored = self.link_state(self.card_id(3))
        assert authored["card"]["archived"] == 0 and authored["links"] == [{"repertoire_id": self.repertoire_id}], authored
        self.report["regressions"].append("test_postgres_graph_bounded_cleanup_preserves_authored_membership_without_step")
        for ordinal in (4, 63_487, NON_OPENING_START, FIXTURE_SIZE - 1):
            state = self.link_state(self.card_id(ordinal))
            assert state["card"]["archived"] == 0 and state["links"], state
        assert self.history() == history_before, "Graph cleanup changed study history"
        self.report["regressions"].append("test_postgres_graph_cleanup_current_pages_restart_and_shared_tail")



    def restore_obsolete_head(self) -> None:
        with psycopg.connect(DATABASE_URL) as database:
            database.execute("INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,0) "
                             "ON CONFLICT DO NOTHING", (self.repertoire_id, self.card_id(0)))
            database.execute("UPDATE cards SET archived=0,repertoire_id=%s WHERE id=%s",
                             (self.repertoire_id, self.card_id(0)))
            database.execute("UPDATE daily_queue SET status='queued' WHERE card_id=%s", (self.card_id(0),))





    def test_postgres_graph_cleanup_generation_replacement_rejects_stale_page(self) -> None:
        self.restore_obsolete_head()
        task = self.task("opening_graph_rebuild")
        prepared = admitted_call(graph.prepare_obsolete_graph_cards, self.repertoire_id, GRAPH_GENERATION, "")
        assert prepared.obsolete_card_ids == (self.card_id(0),)
        before = self.link_state(self.card_id(0))
        with psycopg.connect(DATABASE_URL) as database:
            database.execute("UPDATE background_tasks SET generation=generation+1,state='queued',"
                             "lease_token=NULL,lease_expires_at=NULL WHERE id=%s", (task["id"],))
            database.execute("UPDATE opening_graph_publications SET generation=generation+1 "
                             "WHERE repertoire_id=%s", (self.repertoire_id,))
        with self.capture("graph_stale_generation_publication"):
            with postgres_store.connection(background=True) as database:
                assert not graph.cleanup_graph_cards_in_transaction(database, task, prepared)
        assert self.link_state(self.card_id(0)) == before
        with psycopg.connect(DATABASE_URL) as database:
            database.execute("UPDATE opening_graph_publications SET generation=%s WHERE repertoire_id=%s",
                             (GRAPH_GENERATION, self.repertoire_id))
        self.report["regressions"].append("test_postgres_graph_cleanup_generation_replacement_rejects_stale_page")

    def test_postgres_transaction_timeout_preserves_checkpoint_and_uses_failure_backoff(self) -> None:
        from app import tasks

        self.restore_obsolete_head()
        task = self.task("opening_graph_rebuild", max_attempts=2)
        before = self.link_state(self.card_id(0))
        history_before = self.history()
        original_advance = graph.advance_task_slice_in_transaction

        def timeout_after_effects(database, claimed_task, **options):
            # The actual obsolete-link mutation ran; abort before its durable
            # checkpoint. PostgreSQL closes this connection after the budget.
            assert database.execute_native(
                "SELECT 1 FROM repertoire_cards WHERE repertoire_id=%s AND card_id=%s",
                (self.repertoire_id, self.card_id(0)),
            ).fetchone() is None
            database.execute_native("SELECT pg_sleep(0.075)")
            return original_advance(database, claimed_task, **options)

        with patch.object(tasks.celery_app, "send_task"):
            os.environ["TEMPO_POSTGRES_BACKGROUND_TRANSACTION_TIMEOUT_MS"] = "50"
            try:
                with patch.object(graph, "advance_task_slice_in_transaction", timeout_after_effects), \
                        self.capture("graph_timeout_after_partial_mutation"):
                    assert tasks.execute_background_slice.run(task)
            finally:
                os.environ["TEMPO_POSTGRES_BACKGROUND_TRANSACTION_TIMEOUT_MS"] = "250"
            checkpoint = self.state(task["id"])
            assert checkpoint["state"] == "retrying" and checkpoint["attempt_count"] == 1, checkpoint
            assert checkpoint["phase"] == "cleanup" and checkpoint["payload"] == task["payload"], checkpoint
            assert checkpoint["lease_token"] is None and checkpoint["lease_expires_at"] is None
            assert checkpoint["last_error"] and checkpoint["completed_at"] is None
            assert datetime.fromisoformat(checkpoint["next_attempt_at"]) > datetime.fromisoformat(checkpoint["updated_at"])
            assert self.link_state(self.card_id(0)) == before, "Timed-out mutation escaped rollback"
            assert self.history() == history_before
            postgres_store.close_pools()
            task = self.claim_retry_when_due(task["id"])
            with self.capture("graph_successful_replay_after_timeout"):
                assert tasks.execute_background_slice.run(task)
            checkpoint = self.state(task["id"])
            assert checkpoint["state"] == "queued" and checkpoint["attempt_count"] == 0, checkpoint
            assert checkpoint["last_error"] is None
            assert not self.link_state(self.card_id(0))["links"]
        self.report["regressions"].append("test_postgres_transaction_timeout_preserves_checkpoint_and_uses_failure_backoff")

    def test_postgres_transaction_timeout_exhaustion_stops_repeated_attempts(self) -> None:
        from app import tasks

        self.restore_obsolete_head()
        task = self.task("opening_graph_rebuild", max_attempts=2)
        before = self.link_state(self.card_id(0))

        def always_timeout(database, _task, **_options):
            database.execute_native("SELECT pg_sleep(0.075)")
            raise AssertionError("Expected real PostgreSQL transaction timeout")

        with patch.object(tasks.celery_app, "send_task") as wakeup, \
                patch.object(graph, "advance_task_slice_in_transaction", always_timeout):
            os.environ["TEMPO_POSTGRES_BACKGROUND_TRANSACTION_TIMEOUT_MS"] = "50"
            try:
                with self.capture("graph_timeout_retry_to_terminal"):
                    assert tasks.execute_background_slice.run(task)
                    task = self.claim_retry_when_due(task["id"])
                    assert tasks.execute_background_slice.run(task) is False
            finally:
                os.environ["TEMPO_POSTGRES_BACKGROUND_TRANSACTION_TIMEOUT_MS"] = "250"
            checkpoint = self.state(task["id"])
            assert checkpoint["state"] == "failed" and checkpoint["attempt_count"] == 2, checkpoint
            assert checkpoint["phase"] == "cleanup" and checkpoint["payload"] == task["payload"], checkpoint
            assert checkpoint["last_error"] and checkpoint["completed_at"]
            assert wakeup.call_count == 1, "Terminal timeout scheduled another immediate attempt"
            assert self.link_state(self.card_id(0)) == before
            with psycopg.connect(DATABASE_URL) as database:
                claimable = database.execute(
                    "SELECT 1 FROM background_tasks WHERE id=%s AND state IN ('queued','retrying')",
                    (task["id"],),
                ).fetchone()
            assert claimable is None
        self.report["regressions"].append("test_postgres_transaction_timeout_exhaustion_stops_repeated_attempts")

    def verify_candidate(self) -> None:
        for ordinal, label in ((2, "graph_mostly_current"), (63_485, "graph_nonmatching_tail"),
                               (FIXTURE_SIZE - 1, "graph_raw_exhausted")):
            with self.capture(label):
                prepared = admitted_call(graph.prepare_obsolete_graph_cards, self.repertoire_id, GRAPH_GENERATION,
                                                               self.card_id(ordinal))
            if ordinal == 63_485:
                assert prepared.obsolete_card_ids == () and prepared.checkpoint_card_id is not None
            if ordinal == FIXTURE_SIZE - 1:
                assert prepared.obsolete_card_ids == () and prepared.checkpoint_card_id is None
        self.test_postgres_graph_cleanup_current_pages_restart_and_shared_tail()
        self.test_postgres_graph_cleanup_generation_replacement_rejects_stale_page()
        self.test_postgres_transaction_timeout_preserves_checkpoint_and_uses_failure_backoff()
        self.test_postgres_transaction_timeout_exhaustion_stops_repeated_attempts()
        for regression in self.report["regressions"]:
            print("PASS " + regression)

    def cleanup(self) -> None:
        postgres_store.close_pools()


def validate_disposable_database() -> dict:
    """Refuse before acquiring fixture cleanup ownership or writing anything."""
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("This rehearsal requires the runner-owned disposable PostgreSQL instance")
    expected_schema = max(int(path.name.split("_", 1)[0]) for path in
                          (Path(__file__).resolve().parents[2] / "database/tempo_legacy_database/ddl/migrations").glob("[0-9]*.sql"))
    with psycopg.connect(DATABASE_URL, options="-c default_transaction_read_only=on", row_factory=dict_row) as database:
        metadata = database.execute(
            "SELECT current_database() database_name,current_user database_user,"
            "current_setting('server_version') postgres_version,"
            "current_setting('server_version_num')::integer postgres_version_number,"
            "shobj_description((SELECT oid FROM pg_database WHERE datname=current_database()),"
            "'pg_database') disposable_marker,"
            "(SELECT MAX(version) FROM tempo_schema_migrations) schema_version",
        ).fetchone()
    if metadata["disposable_marker"] != DISPOSABLE_DATABASE_MARKER:
        raise RuntimeError("Refusing graph/retention rehearsal: database lacks the disposable bootstrap marker")
    if metadata["schema_version"] != expected_schema:
        raise RuntimeError("Refusing graph/retention rehearsal: current disposable schema required")
    if metadata["postgres_version_number"] < 180000:
        raise RuntimeError("This rehearsal requires PostgreSQL 18 transaction_timeout support")
    return {key: metadata[key] for key in ("postgres_version", "schema_version", "database_name", "database_user")}


@contextmanager
def owned_admission_scope(owned_database_name):
    """The exclusive helper database has no callers in the parent API workspace."""
    parent_foreground_key = redis_admission_gate._FOREGROUND_KEY
    foreground_key = f'tempo:test:{owned_database_name}:admission:foreground'
    background_key = f'tempo:test:{owned_database_name}:admission:background'
    server = redis_admission_gate.client()
    token = uuid.uuid4().hex
    try:
        with patch.object(redis_admission_gate, '_FOREGROUND_KEY', foreground_key), \
                patch.object(redis_admission_gate, '_BACKGROUND_KEY', background_key):
            # A parent health/study request must not contaminate the isolated
            # rehearsal, and isolation must never clear the parent's lease.
            server.zadd(parent_foreground_key, {token: int(time.time()*1000)+30_000})
            try:
                assert not redis_admission_gate.foreground_present()
                with redis_admission_gate.background_lease():
                    assert server.zscore(parent_foreground_key, token) is not None
            finally:
                server.zrem(parent_foreground_key, token)
            yield
    finally:
        server.delete(foreground_key, background_key)
    assert redis_admission_gate._FOREGROUND_KEY == parent_foreground_key


@contextmanager
def owned_study_policy(owned_database_name):
    """The private database owns its policy; stopped parent clients cannot renew it."""
    from app.services import study_priority

    policy_key = f'tempo:test:{owned_database_name}:study-policy'
    generation_key = policy_key + ':generation'
    server = study_priority.control_client()
    if server.exists(policy_key, generation_key):
        raise RuntimeError('Refusing to replace an existing fixture policy identity')
    try:
        with patch.object(study_priority, 'POLICY_KEY', policy_key), \
                patch.object(study_priority, 'GENERATION_KEY', generation_key), \
                study_priority.policy_publisher():
            yield
    finally:
        server.delete(policy_key, generation_key)


@contextmanager
def owned_fixture_database():
    """Give real queue claims an empty, exclusively fixture-owned database."""
    global DATABASE_URL
    parent_database_url = DATABASE_URL
    owned_database_name = "tempo_incident_" + uuid.uuid4().hex
    owned_database_url = make_conninfo(parent_database_url, dbname=owned_database_name)
    owned_read_database_url = make_conninfo(owned_database_url, options="-c default_transaction_read_only=on")
    owner_marker = DISPOSABLE_DATABASE_MARKER + ":" + owned_database_name
    created = False
    postgres_store.close_pools()
    try:
        with psycopg.connect(parent_database_url, autocommit=True) as parent_database:
            parent_database.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(owned_database_name)))
            created = True
            parent_database.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                sql.Identifier(owned_database_name), sql.Literal(owner_marker)))
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from database.tooling.apply_postgres_migrations import apply_migrations

        apply_migrations(owned_database_url)
        with psycopg.connect(owned_database_url) as database:
            database.execute("INSERT INTO settings(id) VALUES(1)")
            database.execute("INSERT INTO tactic_rotation(id) VALUES(1)")
        DATABASE_URL = owned_database_url
        with patch.dict(os.environ, {"TEMPO_DATABASE_WRITE_URL": owned_database_url,
                                    "TEMPO_DATABASE_READ_URL": owned_read_database_url,
                                    "TEMPO_POSTGRES_BACKGROUND_TRANSACTION_TIMEOUT_MS": "250",
                                    "TEMPO_POSTGRES_BACKGROUND_LOCK_TIMEOUT_MS": "25"}), \
                owned_admission_scope(owned_database_name), owned_study_policy(owned_database_name):
            yield owned_database_name
    finally:
        postgres_store.close_pools()
        DATABASE_URL = parent_database_url
        if created:
            with psycopg.connect(parent_database_url, autocommit=True) as parent_database:
                actual_marker = parent_database.execute(
                    "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=%s",
                    (owned_database_name,),
                ).fetchone()
                if actual_marker is not None:
                    assert actual_marker[0] == owner_marker, "Refusing cleanup of an unowned helper database"
                    parent_database.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(owned_database_name)))
                assert parent_database.execute("SELECT 1 FROM pg_database WHERE datname=%s",
                                               (owned_database_name,)).fetchone() is None


def run(*, mode: str = "candidate", report_path: str | None = None) -> None:
    metadata = validate_disposable_database()
    fixture = IncidentFixture()
    fixture.report["mode"] = mode
    fixture.report.update(metadata)
    fixture.report["database_environment"] = (
        "Owned helper database in the marked disposable cluster. Setup/write use its administrator; "
        "read connections enforce default_transaction_read_only=on. No production role parity claim.")
    repository_root = Path(__file__).resolve().parents[2]
    try:
        fixture.report["revision"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repository_root, text=True, stderr=subprocess.DEVNULL).strip()
        fixture.report["dirty_paths"] = subprocess.check_output(
            ["git", "status", "--short", "--untracked-files=normal"], cwd=repository_root,
            text=True, stderr=subprocess.DEVNULL).splitlines()
    except (OSError, subprocess.CalledProcessError):
        # The slim schema image intentionally has no Git executable. Its
        # enclosing runner report records the revision; hashes identify inputs.
        fixture.report["revision"] = None
        fixture.report["dirty_paths"] = None
    source_paths = ("database/tests/check_postgres_graph_retention.py", "backend/app/services/postgres_opening_graph.py",
                    "backend/app/tasks.py")
    fixture.report["source_sha256"] = {source_path: hashlib.sha256(
        (repository_root / source_path).read_bytes()).hexdigest() for source_path in source_paths}
    try:
        with owned_fixture_database() as owned_database_name:
            fixture.report["owned_database_name"] = owned_database_name
            fixture.report["regressions"].append(
                'test_postgres_graph_rehearsal_admission_isolates_helper_database_and_retains_parent_lease')
            fixture.seed()
            if mode == "baseline":
                fixture.measure_baseline()
            else:
                fixture.verify_candidate()
            fixture.cleanup()
        fixture.report["outcome"] = "passed"
    except Exception:
        fixture.report["outcome"] = "failed"
        raise
    finally:
        if report_path:
            destination = Path(report_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(fixture.report, indent=2) + "\n")
        else:
            print("GRAPH_RETENTION_EVIDENCE " + json.dumps(fixture.report, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("baseline", "candidate"), default="candidate")
    parser.add_argument("--report", help="Save timings and actual handler query plans as JSON")
    arguments = parser.parse_args()
    run(mode=arguments.mode, report_path=arguments.report)


if __name__ == "__main__":
    main()
