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
from test_product_game_lapse_postgres import owned_postgres, case, retained_owner_case


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


def test_collect_only_current_worker_persists_evidence_without_study_effects(case, monkeypatch):
    """Real current composition/SQL; only HTTP and admission controls are supplied.

    No receiving capability is supplied. Existing Study rows are positive
    sentinels, not a fake/no-op Learning port or an empty-schema assertion.
    """
    import hashlib
    import json
    from datetime import date
    from pathlib import Path
    import sys
    import httpx
    from app.games.feedback.review import SyncConfiguration
    from app.product_composition import game_sync_current as current
    from app.product_composition.game_review import configured_job
    from app.queue_commands import issue_queue_head
    from app.contracts.transport.study import ReviewRequest
    from app.services.learning_review_adapter import apply_legacy_review
    from tempo_legacy_database.adapters.learning_bridge import review as review_storage
    from tempo_legacy_database.adapters.games.product_review import PostgresReviewStore
    from tempo_legacy_database.adapters.games.product_sources import PostgresCanonicalGamesStore
    from test_product_feedback_jobs import FakeClock
    from app.contracts.product_v02.games import GameWindow

    repertoire, capture, _, _ = retained_owner_case(case, False)
    queue_day = date.today().isoformat()
    monkeypatch.setattr(review_storage, "configured", lambda: True)
    with case.transaction() as database:
        entry = database.execute_native("""INSERT INTO daily_queue(queue_date,card_id,position,cycle,admission_kind)
            VALUES(%s,%s,0,0,'review') RETURNING id""", (queue_day, case.card_id)).fetchone()[0]
        issued = issue_queue_head(database, dict(queue_date=queue_day, entry_id=entry,
            card_id=case.card_id, expected_revision=1))
        assert issued["issued"], issued
    with case.transaction() as database:
        result = apply_legacy_review(case.card_id, ReviewRequest(outcome="correct",
            queue_entry_id=entry, expected_revision=1, attempt_id="collection-sentinel-" + case.card_id),
            database=database, policy_provider=lambda: SimpleNamespace(timezone="UTC", light_first_interval_days=7))
        assert result["persisted"] is True
    with case.transaction() as database:
        active_entry = database.execute_native("""INSERT INTO daily_queue(queue_date,card_id,position,cycle,admission_kind)
            VALUES(%s,%s,0,1,'review') RETURNING id""", (queue_day, case.card_id)).fetchone()[0]
        issued = issue_queue_head(database, dict(queue_date=queue_day, entry_id=active_entry,
            card_id=case.card_id, expected_revision=1))
        assert issued["issued"], issued
        case.service(database).accept_lapse(case.lapse)

    @contextmanager
    def connections():
        with case.transaction() as database:
            yield database.raw

    @contextmanager
    def fixture_admission():
        yield  # Redis/foreground admission is not this PostgreSQL evidence claim.

    def forbidden(*args, **kwargs):
        pytest.fail("collection constructed or called a receiving capability")
    monkeypatch.setitem(sys.modules, "app.product_composition.provider_priority",
        SimpleNamespace(CurrentProviderPriorityIntake=forbidden))
    monkeypatch.setattr(current, "current_sync_admission", fixture_admission)
    clock = FakeClock()
    configuration = SyncConfiguration(workspace_id="legacy", provider="lichess", account="Learner",
        window=GameWindow(start_at="2026-10-25T00:00:00Z", end_at="2026-10-26T00:00:00Z",
                          precision="millisecond"), repertoire_ids=(repertoire,), speeds=("rapid",),
        rated=True, interval_seconds=60, revision=1)
    review = PostgresReviewStore(connections)
    review.configure(configuration, 0)
    game_id = "collection-" + case.card_id
    original_pgn = ('[White "Learner"]\n[Black "Opponent"]\n[Result "1/2-1/2"]\n'
        '[SetUp "1"]\n[FEN "' + capture["game"]["start_fen"] + '"]\n\n1. d4 1/2-1/2\n')
    requests = []
    def response(request):
        requests.append(str(request.url))
        return httpx.Response(200, headers={"Content-Type": "application/x-ndjson"},
            content=json.dumps(dict(id=game_id, createdAt=1792929600000,
                speed="rapid", rated=True, pgn=original_pgn)) + "\n")

    tables = ("cards", "daily_queue", "queue_attempt_origins", "study_attempts", "reviews",
              "review_attempt_receipts", "study_game_feedback")
    def study_rows():
        with connections() as database:
            return {table: [row[0] for row in database.execute(sql.SQL(
                "SELECT to_jsonb(t) FROM {} t ORDER BY to_jsonb(t)::text").format(
                    sql.Identifier(table))).fetchall()] for table in tables}

    def build_runtime():
        runtime, _ = current.current_provider_worker(workspace="legacy", connections=connections,
            clock=clock, delivery_enabled=False,
            transport_factory=lambda: httpx.MockTransport(response))
        assert runtime.runner.coordinator.learning is None
        assert runtime.runner.coordinator.encounter_feedback is True
        return runtime

    before = study_rows()
    # The ordinary legacy issuance path stores queue_attempt_origins;
    # study_attempts belongs to the separate owner and may legitimately be empty.
    assert all(before[table] for table in tables if table != "study_attempts"), {
        table: len(rows) for table, rows in before.items()}
    assert any(row["queue_entry_id"] == active_entry and row["revision"] == 1
               and row["issued_as_head"] == 1 for row in before["queue_attempt_origins"])
    job = configured_job(configuration, "collection-first-" + case.card_id)
    review.enqueue(configuration, job, clock.now())
    runtime = build_runtime()
    try:
        assert runtime.run_once() == "page"
        assert runtime.run_once() == "idle"
    finally:
        runtime.close()

    # Recreate every reader/store over new connections after the collector closes.
    store = PostgresCanonicalGamesStore(connections)
    pending = store.pending_delivery(clock.now())
    assert pending is not None and pending.encounter is not None and pending.lapse is None
    assert pending.state == "pending" and pending.receipt is None and pending.attempts == 0
    source = store.source(pending.source_key)
    assert source.original.pgn == original_pgn and source.findings and not source.quarantined
    assert source.effects == (pending.encounter,)
    assert store.load_job(job.job_id).window_complete
    with connections() as database:
        retained = database.execute("SELECT payload FROM product_feedback_captures WHERE source_key=%s",
                                    (pending.source_key,)).fetchone()[0]
        assert retained["record"]["pgn"] == original_pgn and retained["findings"]
        assert database.execute("SELECT count(*) FROM imported_games WHERE provider_game_id=%s", (game_id,)).fetchone()[0] == 1
    original_command = pending.model_dump_json()
    review.enqueue(configuration, configured_job(configuration, "collection-overlap-" + case.card_id), clock.now())
    restarted = build_runtime()
    try:
        assert restarted.run_once() == "page"
        assert restarted.run_once() == restarted.run_once() == "idle"
    finally:
        restarted.close()
    assert PostgresCanonicalGamesStore(connections).delivery(pending.operation_id).model_dump_json() == original_command
    with connections() as database:
        assert database.execute("SELECT count(*) FROM product_feedback_deliveries").fetchone()[0] == 1
        assert database.execute("SELECT count(*) FROM product_feedback_sources").fetchone()[0] == 1
        captures = [row[0] for row in database.execute("SELECT payload FROM product_feedback_captures").fetchall()]
        assert captures and all(item["record"]["pgn"] == original_pgn and item["findings"] for item in captures)
    assert len(captures) == 1, "unchanged material rescan must retain the same capture"
    assert len(requests) == 2 and study_rows() == before

    # Persist a genuinely unresolved comparison through the current PostgreSQL
    # collector, then let the actual recurring reconciliation owner revisit it.
    original_game_id = game_id
    game_id = "late-" + case.card_id
    with connections() as database:
        database.execute("UPDATE opening_graph_publications SET state='failed' WHERE repertoire_id=%s", (repertoire,))
    review.enqueue(configuration, configured_job(configuration, "unresolved-" + case.card_id), clock.now())
    unresolved_runtime = build_runtime()
    try:
        assert unresolved_runtime.run_once() == "page"
    finally:
        unresolved_runtime.close()
    with connections() as database:
        unresolved = database.execute("SELECT payload FROM product_feedback_sources WHERE payload->'original'->>'source_game_id'=%s", (game_id,)).fetchone()[0]
        assert unresolved["diagnostic"].startswith("comparison_unavailable:")
        assert not unresolved["findings"]
        database.execute("UPDATE opening_graph_publications SET state='ready' WHERE repertoire_id=%s", (repertoire,))
    from app.games.feedback.continuous import ForwardWindow
    reconciler, recurring = current.current_provider_worker(workspace="legacy", connections=connections,
        clock=clock, delivery_enabled=False, forward_window=ForwardWindow(0),
        reconciliation_interval_seconds=600, transport_factory=lambda: httpx.MockTransport(response))
    try:
        assert recurring.enqueue_due()
        for _ in range(8):
            reconciler.run_once()
    finally:
        reconciler.close()
    with connections() as database:
        resolved = database.execute("SELECT payload FROM product_feedback_sources WHERE source_key=%s", (unresolved["source_key"],)).fetchone()[0]
        assert resolved["findings"] and resolved["diagnostic"] is None
        jobs = [row[0] for row in database.execute("SELECT payload FROM product_feedback_jobs").fetchall()]
        assert any(j["spec"]["job_id"].startswith("reconciling:") and j["window_complete"] for j in jobs)
    assert store.delivery(pending.operation_id).model_dump_json() == original_command
    assert study_rows() == before
    evidence = Path(__file__).resolve().parents[2] / "test-results/collection-only-native"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "readback.json").write_text(json.dumps(dict(
        operation_id=pending.operation_id, source_key=pending.source_key,
        command_sha256=hashlib.sha256(original_command.encode()).hexdigest(),
        pending_attempts=pending.attempts, receiver_supplied=False, captures=len(captures),
        study_rows_unchanged={table: len(rows) for table, rows in before.items()},
        provider_fixture_requests=len(requests)), indent=2))



def test_native_snapshot_bound_envelope_withholds_facts_and_reports_source_sentinel(owned_postgres):
    from app.repertoire import RepertoireSnapshotBoundExceeded
    from tempo_legacy_database.adapters.authored_repertoires.product_snapshot import (
        STATEMENT, read_repertoire, MAX_SOURCE_ROWS, MAX_GRAPH_ROWS, MAX_SNAPSHOT_BYTES, MAX_ROW_BYTES,
    )
    repertoire = "snapshot-bound-" + uuid.uuid4().hex
    with owned_postgres() as database:
        database.execute_native("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,'Synthetic','fixture','2026-10-26')", (repertoire,))
        database.execute_native("""INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)
            SELECT %s || '-' || n::text,%s,'Synthetic','white',%s,'["e2e4"]','2026-10-26'
            FROM generate_series(1,10002) n""", (repertoire, repertoire,
                "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"))
        envelope = database.execute_native(STATEMENT, (repertoire, MAX_SOURCE_ROWS, MAX_GRAPH_ROWS,
            MAX_SNAPSHOT_BYTES, MAX_ROW_BYTES)).fetchone()["snapshot"]
        assert envelope["exists"] and not envelope["bounded"] and envelope["facts"] is None
        assert envelope["line_count"] == 10001  # Real total is larger than the sentinel.
        assert all(envelope[key] == 0 for key in ("card_count", "membership_count", "graph_count"))
        assert 0 < envelope["largest_row"] <= MAX_ROW_BYTES
        assert 0 < envelope["estimated_bytes"] <= MAX_SNAPSHOT_BYTES
        with pytest.raises(RepertoireSnapshotBoundExceeded) as caught:
            read_repertoire(database, repertoire)
        assert caught.value.diagnostic() == dict(code="repertoire_snapshot_exceeds_bound", stage="read",
            sample_complete=False, violations=[dict(dimension="source_rows", observed=10001,
                                                    limit=10000, observation="lower_bound")])
