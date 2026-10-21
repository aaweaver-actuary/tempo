"""Real acceptance adapter/receipt proofs; parent provisions one disposable fixture.

No cluster, migration, dependency or worker is started by this suite. See README.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
import re
from threading import Barrier
import time
import uuid
from unittest.mock import patch

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.errors import LockNotAvailable, UniqueViolation
import pytest

from app import discovery_commands
from tempo_legacy_database.adapters import postgres_store
from app.command_gateway import CommandConflict, execute_command, read_operation
from app.authoring.discovery.acceptance import accept_discovery
from app.authoring.discovery.contracts import DiscoveryChoice, NewDiscoveryIntent
from app.services.discovery_acceptance_store import PostgresDiscoveryAcceptanceStore
from app.services.discovery_admission import admission_intent_id

FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


@pytest.fixture
def fixture_database():
    identity = os.environ.get("TEMPO_DISCOVERY_ACCEPTANCE_FIXTURE", "")
    if not re.fullmatch(r"tempo_discovery_acceptance_[0-9a-f]{32}", identity):
        raise RuntimeError("Parent-owned disposable Discovery acceptance fixture required; see README")
    urls = [os.environ[name] for name in (
        "TEMPO_DISCOVERY_ACCEPTANCE_ADMIN", "TEMPO_DATABASE_READ_URL", "TEMPO_DATABASE_WRITE_URL")]
    targets = [conninfo_to_dict(url) for url in urls]
    if any(target.get("dbname") != identity or target.get("host") not in {"127.0.0.1", "localhost"}
           for target in targets):
        raise RuntimeError("Only the named task-owned loopback fixture database is accepted")
    if len({(target["host"], target.get("port", "5432")) for target in targets}) != 1:
        raise RuntimeError("All fixture connections must target the same owned endpoint")
    if os.environ.get("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("Disposable fixture marker required")
    postgres_store.close_pools()
    with psycopg.connect(urls[0]) as database:
        assert database.execute("SELECT current_database()").fetchone()[0] == identity
    yield urls[0]
    postgres_store.close_pools()


@pytest.fixture
def case(fixture_database):
    identity = "discovery-" + uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    with postgres_store.connection() as database:
        database.execute_native(
            "INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,%s,'fixture',%s)",
            (identity, "Discovery acceptance fixture", now))
        database.execute_native(
            "INSERT INTO repertoire_opportunities(id,repertoire_id,kind,fen_key,status,score,"
            "evidence_json,evidence_fingerprint,created_at,updated_at) "
            "VALUES(%s,%s,'missing_response',%s,'active',1,'{}','revision-one',%s,%s)",
            (identity, identity, " ".join(FEN.split()[:4]), now, now))
    payload = dict(opportunity_id=identity, selected_move_uci="e2e4", evidence_fingerprint="revision-one",
                   repertoire_id=identity, starting_fen=FEN, preview_moves_uci=["e2e4"],
                   recommendation={"move_uci": "e2e4", "preview_moves_uci": ["e2e4"]})
    yield dict(identity=identity, payload=payload, admin=fixture_database)
    # Rows intentionally survive until the parent destroys this disposable database,
    # so failed proofs retain their evidence without touching unrelated resources.


def counts(case):
    identities = [admission_intent_id(case["identity"], "e2e4", revision)
                  for revision in ("revision-one", "revision-two")]
    with postgres_store.connection(read_only=True) as database:
        # Task/event lookups do not join through intents: an orphan task must be
        # visible even if an incorrect adapter committed it and rolled back intent.
        return (
            database.execute_native("SELECT COUNT(*) FROM discovery_admission_intents WHERE opportunity_id=%s",
                                    (case["identity"],)).fetchone()[0],
            database.execute_native("SELECT COUNT(*) FROM background_tasks WHERE kind='discovery_admission' "
                                    "AND deduplication_key=ANY(%s)", (identities,)).fetchone()[0],
            database.execute_native("SELECT COUNT(*) FROM background_task_events WHERE task_id IN ("
                                    "SELECT id FROM background_tasks WHERE kind='discovery_admission' "
                                    "AND deduplication_key=ANY(%s))", (identities,)).fetchone()[0],
        )


def test_simultaneous_acceptance_rechecks_parent_lock_and_creates_one_task(case):
    initial_reads = Barrier(2)
    class SynchronizedStore(PostgresDiscoveryAcceptanceStore):
        first_lookup = True
        def lock_matching_intent(self, choice):
            prior = super().lock_matching_intent(choice)
            if self.first_lookup:
                self.first_lookup = False
                assert prior is None
                initial_reads.wait(timeout=5)
            return prior
    with patch.object(discovery_commands, "PostgresDiscoveryAcceptanceStore", SynchronizedStore):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(execute_command, case["identity"] + suffix, "discovery.accept", case["payload"])
                       for suffix in ("-one", "-two")]
            results = [future.result(timeout=12) for future in futures]
    assert results[0] == results[1] and results[0]["status"] == "preparing"
    assert counts(case) == (1, 1, 1)
    for suffix in ("-one", "-two"):
        assert read_operation(case["identity"] + suffix)["state"] == "complete"


def test_receipt_replay_unique_revision_choice_and_terminal_readmission(case):
    operation_id = case["identity"] + "-accept"
    result = execute_command(operation_id, "discovery.accept", case["payload"])
    assert counts(case) == (1, 1, 1)
    postgres_store.close_pools()
    # Receipt replay ignores refreshed derived preparation and survives reconnect.
    replay = {**case["payload"], "recommendation": {"move_uci": "d2d4"}}
    assert execute_command(operation_id, "discovery.accept", replay) == result
    assert execute_command(case["identity"] + "-logical", "discovery.accept", case["payload"]) == result
    assert counts(case) == (1, 1, 1)
    with pytest.raises(CommandConflict):
        execute_command(operation_id, "discovery.accept", {**case["payload"], "selected_move_uci": "d2d4"})
    with postgres_store.connection() as database:
        row = database.execute_native(
            "SELECT * FROM discovery_admission_intents WHERE id=%s", (result["intent_id"],)).fetchone()
        assert json.loads(row["preview_moves_json"]) == case["payload"]["preview_moves_uci"]
        assert json.loads(row["recommendation_json"]) == case["payload"]["recommendation"]
        existing = NewDiscoveryIntent("different-id", DiscoveryChoice(case["identity"], "e2e4", "revision-one"),
            case["identity"], FEN, ("e2e4",), case["payload"]["recommendation"], row["line_id"], row["created_at"])
        with pytest.raises(UniqueViolation), database.raw.transaction():
            PostgresDiscoveryAcceptanceStore(database).persist_intent_and_task(existing)
        database.execute_native("UPDATE background_tasks SET state='failed' WHERE deduplication_key=%s",
                                (result["intent_id"],))
    assert execute_command(case["identity"] + "-readmit", "discovery.accept", case["payload"]) == result
    assert execute_command(case["identity"] + "-readmit-again", "discovery.accept", case["payload"]) == result
    assert counts(case) == (1, 1, 2)
    with postgres_store.connection(read_only=True) as database:
        task = database.execute_native("SELECT state,generation FROM background_tasks WHERE deduplication_key=%s",
                                       (result["intent_id"],)).fetchone()
        assert (task["state"], task["generation"]) == ("queued", 2)
    with postgres_store.connection() as database:
        database.execute_native("UPDATE repertoire_opportunities SET evidence_fingerprint='revision-two' WHERE id=%s",
                                (case["identity"],))
    next_result = execute_command(case["identity"] + "-revision", "discovery.accept",
                                  {**case["payload"], "evidence_fingerprint": "revision-two"})
    assert next_result["intent_id"] != result["intent_id"]
    assert counts(case) == (2, 2, 3)


@pytest.mark.parametrize("fence", ["prefix", "source", "preview", "missing_preview", "game_scope"])
def test_stale_scope_is_rejected_before_intent_or_task_write(case, fence):
    # Update only this opportunity's owning repertoire, or its observed game fence.
    with postgres_store.connection() as database:
        if fence == "prefix":
            database.execute_native("UPDATE repertoires SET canonical_prefix_revision=canonical_prefix_revision+1 WHERE id=%s",
                                    (case["identity"],))
        elif fence == "game_scope":
            database.execute_native("UPDATE repertoire_opportunities SET game_scope_generation=game_scope_generation-1 WHERE id=%s",
                                    (case["identity"],))
        else:
            database.execute_native("UPDATE repertoires SET canonical_prefix_moves_json='[\"e2e4\"]',"
                "scope_source_revision=3,canonical_prefix_preview_id=%s WHERE id=%s",
                (None if fence == "missing_preview" else "current-preview", case["identity"]))
            database.execute_native("UPDATE repertoire_opportunities SET canonical_scope_source_revision=%s,"
                "canonical_scope_preview_id=%s,game_scope_generation=(SELECT generation FROM repertoire_game_scope WHERE id=1) "
                "WHERE id=%s",
                (2 if fence == "source" else 3,
                 None if fence == "missing_preview" else ("current-preview" if fence == "source" else "old-preview"),
                 case["identity"]))
    operation_id = case["identity"] + "-stale"
    assert execute_command(operation_id, "discovery.accept", case["payload"]) is None
    receipt = read_operation(operation_id)
    assert receipt["state"] == "failed" and receipt["error"]["status_code"] == 404
    assert counts(case) == (0, 0, 0)


@pytest.mark.parametrize("stage", ["intent", "task", "receipt"])
def test_intent_task_and_completed_receipt_rollback_together(case, stage):
    operation_id = case["identity"] + "-rollback"
    if stage == "intent":
        # The intent and opportunity update exist before task persistence fails.
        def fail_task(store, intent_id):
            assert store._database.execute_native(
                "SELECT id FROM discovery_admission_intents WHERE id=%s", (intent_id,)).fetchone()
            raise RuntimeError("injected before durable task")
        context = patch.object(PostgresDiscoveryAcceptanceStore, "requeue_intent", fail_task)
    elif stage == "task":
        original = PostgresDiscoveryAcceptanceStore.persist_intent_and_task
        def fail_after_task(store, intent):
            original(store, intent)
            assert store.task_state(intent.id) == "queued"
            raise RuntimeError("injected after durable task")
        context = patch.object(PostgresDiscoveryAcceptanceStore, "persist_intent_and_task", fail_after_task)
    else:
        # A real trigger fails the completed receipt after all adapter effects.
        with psycopg.connect(case["admin"]) as database:
            database.execute("CREATE FUNCTION discovery_receipt_fault() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN IF NEW.state='complete' THEN "
                "IF NOT EXISTS(SELECT 1 FROM discovery_admission_intents intent JOIN background_tasks task "
                "ON task.deduplication_key=intent.id WHERE intent.opportunity_id=TG_ARGV[0]) THEN "
                "RAISE EXCEPTION 'fixture effects absent'; END IF; "
                "RAISE EXCEPTION 'injected completed discovery receipt'; END IF; RETURN NEW; END $$")
            from psycopg import sql
            database.execute(sql.SQL("CREATE TRIGGER discovery_receipt_fault BEFORE UPDATE ON operation_receipts "
                "FOR EACH ROW WHEN (NEW.operation_id={}) EXECUTE FUNCTION discovery_receipt_fault({})").format(
                    sql.Literal(operation_id), sql.Literal(case["identity"])))
        @contextmanager
        def receipt_fault():
            try:
                yield
            finally:
                with psycopg.connect(case["admin"]) as database:
                    database.execute("DROP TRIGGER discovery_receipt_fault ON operation_receipts")
                    database.execute("DROP FUNCTION discovery_receipt_fault()")
        context = receipt_fault()
    with context:
        if stage == "receipt":
            with pytest.raises(psycopg.errors.RaiseException, match="injected completed discovery receipt"):
                execute_command(operation_id, "discovery.accept", case["payload"])
            assert read_operation(operation_id)["state"] == "unknown"
        else:
            assert execute_command(operation_id, "discovery.accept", case["payload"]) is None
            receipt = read_operation(operation_id)
            expected = "injected before durable task" if stage == "intent" else "injected after durable task"
            assert receipt["state"] == "failed" and receipt["error"]["message"] == expected
    assert counts(case) == (0, 0, 0)
    with postgres_store.connection(read_only=True) as database:
        opportunity = database.execute_native("SELECT admission_state,seen_at FROM repertoire_opportunities WHERE id=%s",
                                              (case["identity"],)).fetchone()
        assert opportunity["admission_state"] is None and opportunity["seen_at"] is None


@pytest.mark.parametrize("blocked_scope", ["repertoire", "game_scope"])
def test_scope_locks_precede_opportunity_lock(case, blocked_scope):
    # Hold one real scope lock, wait until the adapter blocks, then probe locks
    # from a third connection. This proves acquisition order without SQL parsing.
    with psycopg.connect(case["admin"]) as blocker, psycopg.connect(case["admin"]) as observer:
        if blocked_scope == "repertoire":
            blocker.execute("SELECT id FROM repertoires WHERE id=%s FOR UPDATE", (case["identity"],))
        else:
            blocker.execute("SELECT id FROM repertoire_game_scope WHERE id=1 FOR UPDATE")
        with psycopg.connect(case["admin"], row_factory=postgres_store.tempo_row_factory) as raw:
            pid = raw.info.backend_pid
            database = postgres_store.PostgresConnection(raw)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(PostgresDiscoveryAcceptanceStore(database).lock_opportunity_scope, case["identity"])
                try:
                    deadline = time.monotonic() + 5
                    while True:
                        waiting = observer.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", (pid,)).fetchone()
                        observer.commit()
                        if waiting and waiting[0] == "Lock":
                            break
                        if time.monotonic() > deadline:
                            pytest.fail("Adapter never reached the held scope lock")
                        time.sleep(.01)
                    observer.execute("SELECT id FROM repertoire_opportunities WHERE id=%s FOR UPDATE NOWAIT", (case["identity"],))
                    observer.rollback()
                    if blocked_scope == "game_scope":
                        with pytest.raises(LockNotAvailable):
                            observer.execute("SELECT id FROM repertoires WHERE id=%s FOR UPDATE NOWAIT", (case["identity"],))
                        observer.rollback()
                finally:
                    blocker.rollback()
                assert future.result(timeout=5).opportunity.repertoire_id == case["identity"]


def test_adapter_does_not_commit_or_rollback_the_callers_transaction(case):
    identity = admission_intent_id(case["identity"], "e2e4", "revision-one")
    with postgres_store.connection() as database:
        result = accept_discovery(PostgresDiscoveryAcceptanceStore(database), case["payload"], intent_id=identity)
        assert result["intent_id"] == identity
        assert database.execute_native("SELECT 1 FROM discovery_admission_intents WHERE id=%s", (identity,)).fetchone()
        # A second connection sees no effects until the caller chooses to commit.
        assert counts(case) == (0, 0, 0)
        database.rollback()
    assert counts(case) == (0, 0, 0)


@pytest.mark.parametrize("matching", [False, True])
def test_reader_role_preparation_is_nonlocking_and_closes_before_recommendation(case, monkeypatch, matching):
    result = execute_command(case["identity"] + "-seed", "discovery.accept", case["payload"])
    fingerprint = "revision-one" if matching else "unaccepted-revision"
    original_read = discovery_commands.read_connection
    inside_read = False
    @contextmanager
    def observed_read():
        nonlocal inside_read
        with original_read() as database:
            identity = database.execute_native("SELECT current_user,current_setting('transaction_read_only')").fetchone()
            assert (identity[0], identity[1]) == ("tempo_reader", "on")
            database.execute_native("SET LOCAL statement_timeout='1s'")
            inside_read = True
            try:
                yield database
            finally:
                inside_read = False
    recommended = []
    def recommend(opportunity_id):
        assert not inside_read and not matching and opportunity_id == case["identity"]
        recommended.append(opportunity_id)
        return dict(state="ready", evidence_fingerprint=fingerprint,
                    repertoire_id=case["identity"], starting_fen=FEN,
                    candidates=[case["payload"]["recommendation"]])
    monkeypatch.setattr(discovery_commands, "read_connection", observed_read)
    monkeypatch.setattr(discovery_commands, "recommend_missing_continuations", recommend)
    # Keep a real writer lock on the existing intent. Reader hit/miss must neither
    # request a write lock nor wait for this transaction to release it.
    with postgres_store.connection() as writer:
        writer.execute_native("SELECT id FROM discovery_admission_intents WHERE id=%s FOR UPDATE", (result["intent_id"],))
        prepared = discovery_commands.prepare_discovery_acceptance(case["identity"], "e2e4", fingerprint)
    assert prepared["evidence_fingerprint"] == fingerprint and not inside_read
    assert recommended == ([] if matching else [case["identity"]])
    assert ("recommendation" in prepared) is (not matching)
    assert counts(case) == (1, 1, 1)
