"""One explicit disposable PostgreSQL contract; never defaults to a live DSN."""
from __future__ import annotations

from contextlib import contextmanager
import os
import subprocess
import time
from urllib.parse import urlparse
import uuid

import psycopg
from psycopg import sql
import pytest

from tempo_legacy_database.adapters.games.product_feedback import PostgresGamesStore, SCHEMA_SQL
from test_product_feedback_jobs import harness, page, record, spec
from app.games.feedback.jobs import FeedbackRunner


def test_checkpoint_pending_delivery_atomicity_and_restart_readback():
    dsn = os.environ.get("TEMPO_489_TEST_DSN", "")
    container = os.environ.get("TEMPO_489_TEST_CONTAINER", "")
    parsed = urlparse(dsn)
    if parsed.hostname not in {"127.0.0.1", "localhost"} or not parsed.path.lstrip("/").startswith("tempo_489_"):
        pytest.fail("set TEMPO_489_TEST_DSN to an explicitly task-owned loopback tempo_489_* database")
    if not container.startswith("tempo-489-pg-"):
        pytest.fail("TEMPO_489_TEST_CONTAINER must name this task's disposable PostgreSQL container")
    owner = subprocess.check_output(["docker", "inspect", "--format", '{{index .Config.Labels "tempo.task"}}', container], text=True).strip()
    assert owner == "issue-489-disposable", "refuse to restart any resource without this task's label"
    schema = "tempo_489_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as database:
        database.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        database.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        database.execute(SCHEMA_SQL)

    @contextmanager
    def connections():
        with psycopg.connect(dsn, autocommit=True, options=f"-c search_path={schema}", connect_timeout=3) as database:
            yield database

    def count(table):
        with connections() as database:
            return database.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0]

    try:
        store = PostgresGamesStore(connections)
        h = harness(store)
        store.enqueue(spec())
        h.page[0] = page(continuation="safe-page-2", complete=False)
        claimed = store.claim_job(h.clock.now(), h.clock.now() + 120)
        # A real server-side failure after checkpoint/source writes must roll them all back.
        with connections() as database:
            database.execute("CREATE FUNCTION reject_delivery() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'task_fault_before_pending_delivery'; END $$")
            database.execute("CREATE TRIGGER task_fault BEFORE INSERT ON product_feedback_deliveries FOR EACH ROW EXECUTE FUNCTION reject_delivery()")
        with pytest.raises(psycopg.errors.RaiseException, match="task_fault_before_pending_delivery"):
            h.coordinator.capture_page(claimed)
        assert store.load_job("job").continuation is None
        assert count("product_feedback_sources") == count("product_feedback_captures") == count("product_feedback_deliveries") == 0
        with connections() as database:
            database.execute("DROP TRIGGER task_fault ON product_feedback_deliveries")
        h.coordinator.capture_page(claimed)
        checkpoint = store.load_job("job")
        assert checkpoint.continuation == "safe-page-2" and not checkpoint.window_complete
        assert count("product_feedback_sources") == count("product_feedback_captures") == count("product_feedback_deliveries") == 1
        pending = store.pending_delivery(h.clock.now())
        assert pending and pending.receipt is None

        # Restart only this verified disposable server, then recreate the application store.
        subprocess.run(["docker", "restart", container], check=True, capture_output=True, text=True, timeout=30)
        deadline = time.monotonic() + 30
        while True:
            try:
                with connections() as database:
                    database.execute("SELECT 1")
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)
        restored = PostgresGamesStore(connections)
        assert restored.load_job("job") == checkpoint
        assert restored.pending_delivery(h.clock.now()).operation_id == pending.operation_id
        source = restored.source(pending.source_key)
        assert source.original.pgn == "original" and source.normalized.game_id == "provider-game"
        assert source.findings[0].kind == "confirmed_miss"
        h2 = harness(restored)
        h2.coordinator.deliver(restored.pending_delivery(h2.clock.now()))
        with connections() as database:
            saved = database.execute("SELECT payload::text FROM product_feedback_deliveries").fetchone()[0]
        assert '"owner_receipt_id": "owner-receipt-1"' in saved
        assert restored.pending_delivery(h2.clock.now()) is None

        # An overlapping import updates comparison facts without another command.
        restored.enqueue(spec("overlap"))
        FeedbackRunner(h2.coordinator).run_once()
        assert count("product_feedback_deliveries") == 1
        # Correction with no remaining miss still atomically persists a hold/fence command.
        h2.page[0] = page(records=(record(2, "corrected"),))
        h2.coordinator.comparer.compare = lambda *_: ()
        restored.enqueue(spec("correction"))
        FeedbackRunner(h2.coordinator).run_once()
        assert restored.pending_delivery(h2.clock.now()).control is not None
        assert count("product_feedback_captures") == 2
        assert count("product_feedback_deliveries") == 2
    finally:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=3) as database:
            database.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
