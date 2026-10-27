"""One explicit disposable PostgreSQL contract; never defaults to a live DSN."""
from __future__ import annotations

from contextlib import contextmanager
import os
import subprocess
import time
from urllib.parse import urlparse
from types import SimpleNamespace
import uuid

import psycopg
from psycopg import sql
import pytest

from tempo_legacy_database.adapters.games.product_feedback import PostgresGamesStore, SCHEMA_SQL
from test_product_feedback_jobs import encounter_harness, harness, page, record, spec
from app.games.feedback.jobs import FeedbackRunner
from app.contracts.product_v02.learning import EncounterReceipt, GameEncounter


@pytest.fixture
def postgres_workflow():
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

    def restart():
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

    try:
        yield SimpleNamespace(connections=connections, count=count, restart=restart)
    finally:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=3) as database:
            database.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_checkpoint_pending_delivery_atomicity_and_restart_readback(postgres_workflow):
    connections, count = postgres_workflow.connections, postgres_workflow.count
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
    assert count("product_feedback_sources") == count("product_feedback_captures") == count("product_feedback_deliveries") == count("product_feedback_pages") == 0
    with connections() as database:
        database.execute("DROP TRIGGER task_fault ON product_feedback_deliveries")
    h.coordinator.capture_page(claimed)
    checkpoint = store.load_job("job")
    assert checkpoint.continuation == "safe-page-2" and not checkpoint.window_complete
    assert count("product_feedback_sources") == count("product_feedback_captures") == count("product_feedback_deliveries") == 1
    pending = store.pending_delivery(h.clock.now())
    assert pending and pending.receipt is None
    assert count("product_feedback_pages") == 1

    # Restart only this verified disposable server, then recreate the application store.
    postgres_workflow.restart()
    restored = PostgresGamesStore(connections)
    assert restored.load_job("job") == checkpoint
    assert restored.pending_delivery(h.clock.now()).operation_id == pending.operation_id
    source = restored.source(pending.source_key)
    assert source.original.pgn == "original" and source.normalized.game_id == "provider-game"
    assert source.findings[0].kind == "confirmed_miss"
    h2 = harness(restored)
    h2.coordinator.deliver(restored.pending_delivery(h2.clock.now()))
    assert restored.delivery(pending.operation_id).receipt.owner_receipt_id == "owner-receipt-1"
    assert restored.pending_delivery(h2.clock.now()) is None

    # An overlapping import updates comparison facts without another command.
    restored.enqueue(spec("overlap"))
    FeedbackRunner(h2.coordinator).run_once()
    assert count("product_feedback_deliveries") == 1
    # Correction with no remaining miss still atomically persists a hold/fence command.
    h2.page[0] = page(records=(record(2, "corrected"),))
    h2.coordinator.comparer.compare = lambda *_, **__: ()
    restored.enqueue(spec("correction"))
    FeedbackRunner(h2.coordinator).run_once()
    assert restored.pending_delivery(h2.clock.now()).control is not None
    assert count("product_feedback_captures") == 2
    assert count("product_feedback_deliveries") == 2


def test_encounter_lost_ack_restart_and_recomparison_preserve_original_effect(postgres_workflow):
    store = PostgresGamesStore(postgres_workflow.connections)
    h = encounter_harness(store)
    store.enqueue(spec())
    h.runner.run_once()
    pending = store.pending_delivery(h.clock.now())
    assert isinstance(pending.encounter, GameEncounter)
    original = pending.encounter
    h.receiver.uncertain = True
    h.coordinator.deliver(pending)
    assert h.receiver.first_introductions == 1 and store.delivery(pending.operation_id).state == "pending"
    postgres_workflow.restart()
    restored = PostgresGamesStore(postgres_workflow.connections)
    h.store = h.coordinator.store = restored
    h.clock.value += 10
    replay = restored.pending_delivery(h.clock.now())
    assert replay.encounter == original
    h.coordinator.deliver(replay)
    saved = restored.delivery(replay.operation_id)
    assert isinstance(saved.receipt, EncounterReceipt)
    assert saved.receipt.disposition == "first_introduction" and saved.receipt.outcome == "duplicate"
    assert saved.receipt.detected_at == original.detected_at and h.receiver.first_introductions == 1
    h.encounter_state.introduced = True
    h.coordinator.comparer = harness(kinds=("confirmed_miss",)).coordinator.comparer
    restored.enqueue(spec("after-introduction"))
    FeedbackRunner(h.coordinator).run_once()
    source = restored.source(replay.source_key)
    assert source.effects == (original,) and source.encounters[0].decision.introduction == "introduced"
    assert not source.quarantined and postgres_workflow.count("product_feedback_deliveries") == 1


@pytest.mark.parametrize("reason", ["paused", "active_attempt"])
def test_encounter_deferred_receipt_and_correction_hold_are_durable(postgres_workflow, reason):
    store = PostgresGamesStore(postgres_workflow.connections)
    h = encounter_harness(store)
    h.receiver.deferred_reason = reason
    store.enqueue(spec())
    h.runner.run_once()
    pending = store.pending_delivery(h.clock.now())
    h.coordinator.deliver(pending)
    restored = PostgresGamesStore(postgres_workflow.connections)
    saved = restored.delivery(pending.operation_id)
    assert isinstance(saved.receipt, EncounterReceipt)
    assert saved.receipt.deferred_reason == reason and saved.receipt.admission_status == "pending"
    assert saved.receipt.available_by is None and h.receiver.first_introductions == 0
    h.store = h.coordinator.store = restored
    h.page[0] = page(records=(record(2, "corrected"),))
    restored.enqueue(spec("corrected-before-admission"))
    FeedbackRunner(h.coordinator).run_once()
    control = restored.pending_delivery(h.clock.now())
    assert control.control is not None and control.control.key == pending.encounter.key
    assert restored.source(pending.source_key).quarantined
    h.coordinator.deliver(control)
    assert restored.delivery(control.operation_id).receipt.status == "held"
    assert h.receiver.accept_encounter(pending.encounter).status == "held"
    assert h.receiver.first_introductions == 0
