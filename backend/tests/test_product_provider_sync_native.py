"""Current provider publication -> public intake -> independent real Study.

One existing capped, task-owned PostgreSQL fixture; injected synthetic provider
HTTP only. No host DSN, Redis, provider account, browser or service is touched.
Run only this file through the existing shared validation lock/native runner.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest

from app.contracts.product_v02.games import GameWindow
from app.games.feedback.coordinator import stable_id
from app.games.feedback.review import SyncConfiguration
from app.product_composition.game_sync_current import current_provider_worker
from app.product_composition.retained_game import retained_canonical_game
from app.repertoire import project_capture
from app.services.postgres_integrity import current_integrity_certificate
from product_encounter_fixtures import retained_history_case
from tempo_legacy_database.adapters.games.product_review import PostgresReviewStore
from test_product_encounter_integration import _study_refresh, normal_study_clock
from test_product_feedback_integration import _issue, _ordinary_review
from test_product_game_lapse import NOW
from test_product_game_lapse_postgres import owned_postgres


def test_native_review_commands_commit_with_receipt_replay_and_rollback(owned_postgres, monkeypatch):
    from fastapi import HTTPException
    from app import command_gateway as gateway
    from app.product_composition import game_review_commands as commands
    monkeypatch.setattr(gateway,"_handlers",{})
    monkeypatch.setattr(gateway,"_preparers",{})
    monkeypatch.setattr(gateway,"_writer_connection",lambda background:owned_postgres())
    commands.register_commands()
    configuration=dict(workspace_id="legacy",provider="lichess",account="Learner",
        window=dict(start_at="2026-10-25T00:00:00Z",end_at="2026-10-26T00:00:00Z",precision="second"),
        repertoire_ids=["white"],speeds=["rapid"],rated=True,interval_seconds=None,revision=1)
    payload=dict(configuration=configuration,expected_revision=0)
    saved=gateway.execute_command("native-config","product_games.configure",payload)
    assert saved==gateway.execute_command("native-config","product_games.configure",payload)
    run=dict(provider="lichess",operation_id="native-run")
    queued=gateway.execute_command("native-run","product_games.sync",run)
    assert queued["status"]=="pending"
    assert queued==gateway.execute_command("native-run","product_games.sync",run)
    with pytest.raises(gateway.CommandConflict):
        gateway.execute_command("native-run","product_games.sync",dict(provider="chesscom",operation_id="native-run"))
    original=gateway._handlers["product_games.sync"]
    def failed_after_enqueue(database,payload):
        original(database,payload)
        raise HTTPException(409,"synthetic_failure_after_enqueue")
    monkeypatch.setitem(gateway._handlers,"product_games.sync",failed_after_enqueue)
    assert gateway.execute_command("native-rollback","product_games.sync",
        dict(provider="lichess",operation_id="native-rollback")) is None
    with owned_postgres() as database:
        assert database.execute_native("SELECT COUNT(*) FROM product_feedback_jobs").fetchone()[0]==1
        assert database.execute_native("SELECT COUNT(*) FROM product_feedback_sync_requests").fetchone()[0]==1
        rows=database.execute_native("SELECT operation_id,state FROM operation_receipts ORDER BY operation_id").fetchall()
        assert [tuple(row) for row in rows]==[("native-config","complete"),("native-rollback","failed"),("native-run","complete")]


class FixtureConnections:
    def __init__(self, transaction):
        self.writer = transaction

    @contextmanager
    def __call__(self):
        with self.writer() as database:
            yield database.raw


class FixtureClock:
    def __init__(self):
        self.value = NOW.timestamp()

    def now(self):
        return self.value

    def utc_now(self):
        return datetime.fromtimestamp(self.value, timezone.utc).isoformat()


def test_current_both_provider_publication_restart_and_real_study_are_one_pipeline(
    owned_postgres, monkeypatch, normal_study_clock,
):
    from app import legacy_game_observation
    from app.services import redis_admission_gate, study_priority
    from tempo_legacy_database.adapters import postgres_store

    # Select only the isolated fixture boundary. Admission still runs through
    # the actual application gate, without borrowing any host Redis endpoint.
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    monkeypatch.setattr(redis_admission_gate, "configured", lambda: False)
    monkeypatch.delenv("TEMPO_FOREGROUND_ACTIVITY_URL", raising=False)
    monkeypatch.setenv("TEMPO_PRODUCT_GAME_SYNC_ENABLED", "1")
    monkeypatch.setattr(study_priority, "control_snapshot",
        lambda: dict(available=True, active=False, study_only_mode=True))
    clock = FixtureClock()
    with retained_history_case(owned_postgres, state="unintroduced", ordinary_limit=0,
                               study_only=True) as case:
        with case.transaction() as database:
            protected = _issue(database, case.protected_id)
            start_fen = database.execute_native("SELECT start_fen FROM cards WHERE id=%s",
                                               (case.card_id,)).fetchone()[0]
        before = case.readback()
        connections = FixtureConnections(case.transaction)
        captures = {}
        capture_game = legacy_game_observation.capture_game

        def capture(repertoire_id, game_id, decision_plies=None):
            with case.transaction() as database:
                def reader(statement, parameters=()):
                    return [dict(row) for row in database.execute_native(statement, parameters).fetchall()]

                def metadata(selected_repertoire):
                    row = database.execute_native("""SELECT repertoire.scope_source_revision,
                        repertoire.canonical_prefix_revision,scope.generation catalogue_generation,
                        publication.generation graph_generation,publication.state graph_state,
                        integrity.status integrity_status,integrity.scan_status,integrity.scan_generation
                        FROM repertoires repertoire CROSS JOIN repertoire_game_scope scope
                        JOIN opening_graph_publications publication ON publication.repertoire_id=repertoire.id
                        JOIN repertoire_integrity_state integrity ON integrity.repertoire_id=repertoire.id
                        WHERE repertoire.id=%s AND scope.id=1""", (selected_repertoire,)).fetchone()
                    certificate = current_integrity_certificate(database, selected_repertoire, 1)
                    return {**dict(row), "integrity_current": certificate is not None,
                            "integrity_certificate": certificate}

                value = capture_game(repertoire_id, game_id, decision_plies, reader=reader,
                                     metadata=metadata, now=NOW)
            captures[game_id] = value
            return value

        monkeypatch.setattr(legacy_game_observation, "capture_game", capture)

        def pgn(move, *, target=True):
            setup = f'[SetUp "1"]\n[FEN "{start_fen}"]\n' if target else ""
            return ('[White "Learner"]\n[Black "Opponent"]\n[Result "1/2-1/2"]\n'
                    + setup + f'\n1. {move} 1/2-1/2\n')

        occurred = NOW - timedelta(days=1)
        lichess = [dict(id=f"provider-native-{case.card_id}-{index}",
            createdAt=int(occurred.timestamp() * 1000) + index, speed="rapid", rated=True,
            pgn=pgn("d4", target=index == 0)) for index in range(101)]
        seen = []
        outage = [True]

        def http(request):
            seen.append((request.url.host, request.url.path))
            if request.url.host == "lichess.org":
                if outage[0]:
                    outage[0] = False
                    return httpx.Response(429, headers={"Retry-After": "5"})
                parameters = request.url.params
                selected = [row for row in lichess
                    if int(parameters["since"]) <= row["createdAt"] < int(parameters["until"])]
                return httpx.Response(200, content="\n".join(json.dumps(row)
                    for row in selected[:int(parameters["max"])]),
                    headers={"Content-Type": "application/x-ndjson"})
            base = "https://api.chess.com/pub/player/learner/games/"
            if request.url.path.endswith("archives"):
                return httpx.Response(200, json={"archives": [base+"2026/09", base+"2026/10"]})
            month = request.url.path[-2:]
            return httpx.Response(200, json={"games": [dict(
                uuid=f"provider-native-{case.card_id}-{month}",
                url="https://www.chess.com/game/live/123", pgn=pgn("e4" if month == "09" else "d4"),
                end_time=int(datetime(2026, int(month), 25, 12, tzinfo=timezone.utc).timestamp()),
                rules="chess", time_class="rapid", time_control="600", rated=True)]})

        window = GameWindow(start_at="2026-09-01T00:00:00Z", end_at="2026-11-01T00:00:00Z",
                            precision="millisecond")
        review_store = PostgresReviewStore(connections)
        for provider in ("lichess", "chesscom"):
            review_store.configure(SyncConfiguration(workspace_id="legacy", provider=provider,
                account="Learner", window=window, repertoire_ids=(case.repertoire_id,),
                speeds=("rapid",), rated=True, interval_seconds=None, revision=1), 0)

        def worker():
            return current_provider_worker(workspace="legacy", connections=connections,
                clock=clock, transport_factory=lambda: httpx.MockTransport(http))

        runtime, _ = worker()
        coordinator = runtime.runner.coordinator
        from app.product_composition.game_review import create_game_review
        review = create_game_review(workspace="legacy", connections=connections,
            learning=coordinator.learning, clock=clock, runtime=runtime)
        for provider in ("lichess", "chesscom"):
            review.sync(provider=provider, operation_id=f"provider-native:{provider}:{case.card_id}")

        # Lose exactly one acknowledgment after the receiving owner committed.
        intake = coordinator.learning
        accept = intake.accept_encounter
        lost = []

        def accept_then_lose(command):
            receipt = accept(command)
            if not lost:
                lost.append((command, receipt))
                raise ConnectionError("synthetic acknowledgment loss after owner commit")
            return receipt

        intake.accept_encounter = accept_then_lose
        for _ in range(20):
            runtime.run_once()
            if lost:
                break
        assert lost, "the actual owner must commit before the simulated crash"
        assert case.readback() == before  # Intake did not refresh, introduce or mutate an active attempt.
        command, receipt = lost[0]
        assert receipt.admission_status == "pending" and receipt.disposition == "first_introduction"
        runtime.close()

        # A new process assembly reuses the durable page and original command.
        clock.value += 10
        runtime, _ = worker()
        coordinator = runtime.runner.coordinator
        try:
            for _ in range(125):
                result = runtime.run_once()
                if result == "idle":
                    pending = [review_store.last_job("legacy", provider)
                               for provider in ("lichess", "chesscom")]
                    retry_times = [job.next_attempt_at for job in pending
                                   if job.status == "retryable" and job.next_attempt_at > clock.now()]
                    if retry_times:
                        # A 429 can occur after restart, depending on durable
                        # claim order. Respect its actual persisted Retry-After.
                        clock.value = min(retry_times)
                        continue
                    break
            else:
                raise AssertionError("bounded provider fixture did not drain")
            jobs = [review_store.last_job("legacy", provider) for provider in ("lichess", "chesscom")]
            assert all(job.status == "complete" and job.window_complete for job in jobs), [
                (job.spec.provider,job.status,job.pages_committed,job.error,job.next_attempt_at)
                for job in jobs]
            with case.transaction() as database:
                source_count = database.execute_native("SELECT COUNT(*) FROM product_feedback_sources").fetchone()[0]
                sources = database.execute_native("SELECT payload::text FROM product_feedback_sources").fetchall()
                rows = database.execute_native("SELECT payload::text FROM product_feedback_deliveries").fetchall()
                assert source_count == 103
            from app.games.feedback.ports import Delivery, SourceState
            findings = tuple(finding for row in sources
                for finding in SourceState.model_validate_json(row[0]).findings)
            assert sum(finding.kind == "known_unintroduced" for finding in findings) == 3
            # Different starting positions remain explicit unknown authority;
            # absence of an eligible lapse must never mean all games were correct.
            assert sum(finding.kind == "known_unavailable" for finding in findings) == 100
            deliveries = tuple(Delivery.model_validate_json(row[0]) for row in rows)
            assert len(deliveries) == 3 and all(item.state == "acknowledged" for item in deliveries)
            assert {item.encounter.observation for item in deliveries} == {"matched", "missed"}
            replay = coordinator.learning.accept_encounter(command)
            assert replay.owner_receipt_id == receipt.owner_receipt_id
            assert replay.detected_at == receipt.detected_at and case.readback() == before
            assert sum(host == "lichess.org" for host, _ in seen) >= 3  #429 + more than one page.
            assert any(path.endswith("2026/09") for _, path in seen)
            assert any(path.endswith("2026/10") for _, path in seen)
            # Generic current snapshots and retained captures share the actual
            # publication generation; no authored counter is relabeled.
            source = coordinator.store.source(stable_id("legacy", command.key.provider,
                command.key.game_id.split(":", 1)[1]))
            assert source is not None and source.normalized.game_id == command.key.game_id
            assert all(item.encounter.decision.repertoire_revision == 1 for item in deliveries)
            capture = captures[command.key.game_id]
            snapshot = project_capture(capture, comparison_game=retained_canonical_game(capture))
            available = _study_refresh(case, capture, snapshot, command)
            assert available.admission_status == "available"
            assert available.owner_receipt_id == receipt.owner_receipt_id
            introduced = case.readback()
            assert introduced["reviews"] == before["reviews"]
            assert tuple(row for row in introduced["origins"] if row[1] == case.protected_id) == before["origins"]
            with case.transaction() as database:
                _ordinary_review(database, case.protected_id, protected, "protected-provider:"+case.card_id)
                target = _issue(database, case.card_id)
                assert _ordinary_review(database, case.card_id, target,
                                        "first-provider:"+case.card_id)["persisted"]
            learned = case.readback()
            settled = coordinator.learning.accept_encounter(command)
            assert settled.owner_receipt_id == receipt.owner_receipt_id
            assert settled.admission_status == "settled" and case.readback() == learned
            with case.transaction() as database:
                assert database.execute_native("SELECT COUNT(*) FROM reviews WHERE card_id=%s AND source_kind='game_lapse'",
                                               (case.card_id,)).fetchone()[0] == 0
                assert tuple(database.execute_native("SELECT new_cards_per_day,study_only_mode FROM settings WHERE id=1").fetchone()) == (0, 1)
        finally:
            runtime.close()
