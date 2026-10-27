"""Prepared, not yet qualified: new-provider publication to actual Study replay.

Run only in the coordinated native slot. Reuses the existing capped, labelled
container owner; source migrations 001..052 apply only to that empty disposable
database. This does not change installed readiness/roles or activate a worker.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
import importlib.util
import json
from pathlib import Path

import httpx
import psycopg
from psycopg.conninfo import make_conninfo
import pytest

from app.contracts.product_v02.learning import EncounterReceipt
from app.games.feedback.review import SyncConfiguration
from app.product_composition.encounters import prepare_captured_encounters, receive_admitted_encounter
from app.product_composition.game_review import configured_job
from app.product_composition.game_sync_current import create_current_provider_worker
from app.study.encounter_feedback import EncounterIntroductionPolicy, encounter_digest
from product_encounter_fixtures import retained_history_case
from tempo_legacy_database.adapters.games.product_review import DedicatedGamesConnections, PostgresReviewStore
from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory
from test_product_game_lapse import NOW
from test_product_introductions_postgres import owned_postgres

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def provider_database(owned_postgres):
    spec = importlib.util.spec_from_file_location("provider_receiving_migrations",
        ROOT / "database/tooling/apply_postgres_migrations.py")
    migrations = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migrations)
    files = sorted(migrations.MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
    migrations.validate_migration_history(files, [], expected_version=52)
    with psycopg.connect(**owned_postgres) as raw:
        for path in files:
            registration = migrations.MODULE_MIGRATIONS.get(path.name)
            if registration:
                source = (ROOT / "database" / registration[2]).read_bytes()
                assert hashlib.sha256(source).hexdigest() == registration[3]
                raw.execute(source.decode(), prepare=False)
            raw.execute(path.read_text(), prepare=False)
            assert raw.execute("SELECT MAX(version) FROM tempo_schema_migrations").fetchone()[0] == int(path.name[:3])
        raw.execute("INSERT INTO settings(id) VALUES(1)")

    @contextmanager
    def transaction():
        with psycopg.connect(**owned_postgres, row_factory=tempo_row_factory) as raw:
            yield PostgresConnection(raw)
    return transaction, make_conninfo(**owned_postgres)


@pytest.fixture
def admitted_controls(monkeypatch):
    from app.services import redis_admission_gate, study_priority
    monkeypatch.setenv("TEMPO_PRODUCT_GAME_SYNC_ENABLED", "1")
    monkeypatch.delenv("TEMPO_FOREGROUND_ACTIVITY_URL", raising=False)
    monkeypatch.setattr(redis_admission_gate, "configured", lambda: False)
    # Native harness substitutes only the external control transport. The real
    # current admission context and the receiver's background check still run.
    monkeypatch.setattr(study_priority, "control_snapshot", lambda:
        dict(available=True, active=False, study_only_mode=True))


class Clock:
    def now(self):
        return NOW.timestamp()

    def utc_now(self):
        return NOW.isoformat()


@pytest.mark.parametrize("provider", ["lichess", "chesscom"])
def test_provider_page_commit_actual_admission_and_retained_replay_share_one_effect(
        provider_database, admitted_controls, provider):
    transaction, dsn = provider_database
    with retained_history_case(transaction, state="unintroduced", ordinary_limit=0, study_only=True) as case:
        with transaction() as database:
            if provider == "chesscom":
                # Retain the opaque canonical ID; provider is never inferred from it.
                database.execute_native("UPDATE imported_games SET provider='chess.com' WHERE id=%s", (case.game_ids[0],))
            database.execute_native("UPDATE imported_games SET result='draw' WHERE id=%s", (case.game_ids[0],))
            raw = dict(database.execute_native("SELECT * FROM imported_games WHERE id=%s", (case.game_ids[0],)).fetchone())
            workspace = database.execute_native("SELECT workspace_id FROM legacy_learning_identity WHERE id=1").fetchone()[0]
        pgn = ('[Event "Synthetic provider receiving"]\n[White "synthetic"]\n[Black "opponent"]\n'
            '[Result "1/2-1/2"]\n[SetUp "1"]\n[FEN "'+raw["start_fen"]+'"]\n\n1. d4 1/2-1/2\n')
        played = datetime.fromisoformat(raw["played_at"])
        timestamp = int(played.timestamp())
        archive = "https://api.chess.com/pub/player/synthetic/games/" + played.strftime("%Y/%m")
        calls = []
        def response(request):
            calls.append(str(request.url))
            if request.url.host == "lichess.org":
                value = dict(id=raw["provider_game_id"], createdAt=timestamp*1000,
                    speed="rapid", rated=False, pgn=pgn)
                return httpx.Response(200, content=json.dumps(value)+"\n")
            if request.url.path.endswith("archives"):
                return httpx.Response(200, json=dict(archives=[archive]))
            return httpx.Response(200, json=dict(games=[dict(uuid=raw["provider_game_id"],
                url="https://www.chess.com/game/live/123", pgn=pgn, rules="chess",
                end_time=timestamp, time_class="rapid", time_control="600", rated=False)]))
        configuration = SyncConfiguration(workspace_id=workspace, provider=provider,
            account="synthetic", window=dict(start_at=(played-timedelta(seconds=30)).isoformat(),
                end_at=(played+timedelta(seconds=30)).isoformat(), precision="second"),
            repertoire_ids=(case.repertoire_id,), speeds=("rapid",), rated=None,
            interval_seconds=None, revision=1)
        writer = DedicatedGamesConnections(dsn)
        reader = DedicatedGamesConnections(dsn, read_only=True)
        review = PostgresReviewStore(writer)
        review.configure(configuration, 0)
        runtime, _ = create_current_provider_worker(workspace=workspace,
            connections=writer, read_connections=reader, clock=Clock(),
            transport_factory=lambda: httpx.MockTransport(response))
        receiver = runtime.runner.coordinator.learning
        # Exact read transport injection, never a fake receipt or Study owner.
        receiver.capture = lambda command: case.capture(command.key.game_id)
        store = runtime.runner.coordinator.store
        request = configured_job(configuration, "native-provider-" + case.card_id)
        store.enqueue(request)
        try:
            assert runtime.run_once() == "page"
            job = store.load_job(request.job_id)
            assert job.window_complete and job.status == "complete", job.error
            original = store.pending_delivery(NOW.timestamp())
            assert original is not None and original.encounter is not None
            runtime.close()
            runtime, _ = create_current_provider_worker(workspace=workspace,
                connections=writer, read_connections=reader, clock=Clock(),
                transport_factory=lambda: httpx.MockTransport(response))
            receiver = runtime.runner.coordinator.learning
            receiver.capture = lambda command: case.capture(command.key.game_id)
            store = runtime.runner.coordinator.store
            assert store.pending_delivery(NOW.timestamp()) == original
            assert runtime.run_once() == "delivery"
            receipt = receiver.encounter_status(original.command.key)
            assert isinstance(receipt, EncounterReceipt)
            assert receipt.disposition == "first_introduction" and receipt.admission_status == "available"
            assert store.pending_delivery(NOW.timestamp()) is None
            learned = case.readback()
            assert not learned["reviews"]
            assert len([entry for entry in learned["queue"] if entry[1] == case.card_id]) == 1
            with transaction() as database:
                settings = tuple(database.execute_native("SELECT new_cards_per_day,study_only_mode FROM settings WHERE id=1").fetchone())
                assert settings == (0, 1)
                assert database.execute_native("SELECT COUNT(*) FROM study_game_feedback WHERE card_id=%s", (case.card_id,)).fetchone()[0] == 1
            recaptured = prepare_captured_encounters(case.capture(case.game_ids[0]), detected_at=NOW.isoformat())
            replay, = recaptured.encounters
            assert replay.key == original.command.key and replay.source == original.encounter.source
            assert encounter_digest(replay) == encounter_digest(original.encounter)
            with transaction() as database:
                duplicated = receive_admitted_encounter(database, capture=recaptured.capture,
                    snapshot=recaptured.snapshot, command=replay, operation_stopped=lambda: False,
                    introduction_policy=EncounterIntroductionPolicy("encounter_exception"), clock=lambda: NOW)
            assert duplicated.owner_receipt_id == receipt.owner_receipt_id and duplicated.outcome == "duplicate"
            assert case.readback() == learned and calls
        finally:
            runtime.close()
            # All of these rows belong to this synthetic request/workspace.
            with writer() as database:
                database.execute("DELETE FROM product_feedback_retry_requests WHERE workspace_id=%s", (workspace,))
                database.execute("DELETE FROM product_feedback_sync_requests WHERE workspace_id=%s", (workspace,))
                database.execute("DELETE FROM product_feedback_deliveries")
                database.execute("DELETE FROM product_feedback_captures")
                database.execute("DELETE FROM product_feedback_sources")
                database.execute("DELETE FROM product_feedback_pages")
                database.execute("DELETE FROM product_feedback_jobs")
                database.execute("DELETE FROM product_feedback_configuration")
