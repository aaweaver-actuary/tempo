"""One-time historical operators: synthetic HTTP, saved pages and scoped replay."""
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import json
from threading import Event
from types import SimpleNamespace

import httpx
import pytest

from app.games.feedback.ports import DeliveryDeferred
from app.games.feedback.jobs import FeedbackRunner
from app.product_composition.game_history_scripts import (
    DeferredSnapshots, RetainedPages, comparison_runner, load_manifest, new_manifest,
    report, run_history, save_manifest,
)
from app.product_composition.game_sync import create_game_sync
from app.games.feedback.review import SyncConfiguration
from test_product_feedback_jobs import FakeClock, MemoryStore
from test_product_first_boundary import snapshot
from test_product_game_lapse_postgres import owned_postgres


class HistoryStore(MemoryStore):
    def claim_job(self, now, lease_until, *, job_id=None):
        job = self.load_job(job_id)
        if job is None or not ((job.status in {"queued", "retryable"} and job.next_attempt_at <= now)
                or (job.status == "running" and job.lease_until <= now)):
            return None
        self.save_job(job.model_copy(update={"status": "running", "lease_until": lease_until,
            "attempts": job.attempts + 1}))
        return self.load_job(job_id)

    def retained_page(self, job_id, continuation):
        return self.pages[job_id, continuation]

    def bind_source(self, game, **kwargs):
        return game

    def pending_delivery(self, now, **kwargs):
        return super().pending_delivery(now)


def setup_scope(store=None):
    from app.contracts.product_v02.games import GameWindow
    store = store or HistoryStore()
    configuration = SyncConfiguration(workspace_id="fixture", provider="lichess", account="Learner",
        window=GameWindow(start_at="2026-10-01T00:00:00Z", end_at="2026-10-02T00:00:00Z", precision="second"),
        repertoire_ids=("rep",), speeds=("rapid",), rated=True, interval_seconds=None)
    manifest = new_manifest("fixture", (configuration,), now=datetime(2026, 10, 30, tzinfo=timezone.utc))
    review = SimpleNamespace(configuration=lambda workspace, provider: configuration,
        deliveries=lambda key: tuple(d for d in store.deliveries.values() if d.source_key == key))
    return store, configuration, manifest, review


def rows(moves=("d4",)):
    return [dict(id=f"game-{i}", createdAt=1792929600000 + i * 1000,
        speed="rapid", rated=True,
        pgn=f'[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n1. {move} 1-0\n')
        for i, move in enumerate(moves)]


def collect(store, configuration, manifest, review, *, records=None, maximum_units=50, transport=None):
    from app.product_composition.game_history_scripts import configuration_policy
    records = records if records is not None else rows()
    def factory(configuration):
        runtime = create_game_sync(policies=(configuration_policy(configuration),), store=store,
            snapshots=DeferredSnapshots(), clock=FakeClock(), delivery_enabled=False,
            source_identity=store, encounter_feedback=True, preserve_existing_comparison=True,
            transport_factory=lambda: transport or httpx.MockTransport(lambda request: httpx.Response(200,
                content="\n".join(json.dumps(row) for row in records),
                headers={"Content-Type": "application/x-ndjson"})))
        return runtime.runner
    return run_history(manifest, store=store, review=review, clock=FakeClock(),
        admission=nullcontext, stopped=Event(), runner_factory=factory, maximum_units=maximum_units)


class Intake:
    def __init__(self):
        self.calls = []
    def effect_identity(self, key):
        return key.logical_id()
    def prepare_first_boundary(self, *args, **kwargs):
        pytest.fail("supplied snapshot already has exact mappings")
    def prepare(self, delivery):
        raise DeliveryDeferred("synthetic_owner_hold", retry_after=30)


def compare(store, configuration, manifest, review, *, snapshots=None, maximum_units=50, intake=None, clock=None):
    intake = intake or Intake()
    clock = clock or FakeClock()
    return run_history(manifest, store=store, review=review, clock=clock,
        admission=nullcontext, stopped=Event(), maximum_units=maximum_units, compare=True,
        runner_factory=lambda c: comparison_runner(store=store, snapshots=snapshots or
            SimpleNamespace(snapshot=lambda _: snapshot(introduction="introduced")),
            intake=intake, clock=clock, spec=manifest.job(c)))


def test_manifest_calendar_clamp_and_roundtrip_never_overwrites(tmp_path):
    _, configuration, _, _ = setup_scope()
    manifest = new_manifest("fixture", (configuration,), now=datetime(2026, 5, 31, 12, tzinfo=timezone.utc))
    assert manifest.window.start_at == "2026-02-28T12:00:00+00:00"
    path = tmp_path / "history.json"
    save_manifest(path, manifest)
    assert path.stat().st_mode & 0o777 == 0o600
    assert load_manifest(path) == manifest
    with pytest.raises(FileExistsError):
        save_manifest(path, manifest)
    content = json.loads(path.read_text())
    content["manifest"]["run_id"] = "changed"
    path.write_text(json.dumps(content))
    with pytest.raises(ValueError, match="digest_mismatch"):
        load_manifest(path)


def test_historical_collection_then_offline_first_departure_and_resume(monkeypatch):
    store, configuration, manifest, review = setup_scope()
    # Own departure, opponent departure followed by an own deviation, covered game.
    assert collect(store, configuration, manifest, review, records=rows(("d4", "e4 c5 2. d4", "e4 e5 2. Nf3"))) == 1
    assert report(store, review, manifest)["complete"]
    assert len(store.sources) == 3 and not store.deliveries
    # No acquisition is permitted after collection.
    monkeypatch.setattr(httpx.AsyncClient, "__init__", lambda *a, **kw: pytest.fail("offline replay attempted HTTP"))
    assert compare(store, configuration, manifest, review) == 2  # page + one held delivery
    result = report(store, review, manifest, compare=True)
    assert result["selections"] == {"own_actionable": 1, "opponent_gap": 1, "covered": 1}
    assert not result["complete"] and result["delivery_states"] == {"pending": 1}
    delivery, = store.deliveries.values()
    assert delivery.command.key.occurrence_ply == 1
    assert delivery.command.observation == "missed"
    assert delivery.qualification_reason == "synthetic_owner_hold"
    original = delivery.command.model_dump_json()
    assert compare(store, configuration, manifest, review) == 0
    assert len(store.deliveries) == 1
    assert next(iter(store.deliveries.values())).command.model_dump_json() == original


def test_partial_import_blocks_comparison_and_resumes_at_exact_cursor():
    store, configuration, manifest, review = setup_scope()
    records = rows(tuple("d4" for _ in range(101)))
    calls = []
    def http(request):
        calls.append(int(request.url.params["since"]))
        # Real provider pagination asks for an overlapping last timestamp.
        selected = [r for r in records if r["createdAt"] >= calls[-1]][:100]
        return httpx.Response(200, content="\n".join(json.dumps(r) for r in selected),
            headers={"Content-Type": "application/x-ndjson"})
    transport = httpx.MockTransport(http)
    assert collect(store, configuration, manifest, review, maximum_units=1, transport=transport) == 1
    assert not report(store, review, manifest)["complete"]
    with pytest.raises(ValueError, match="historical_import_not_complete"):
        compare(store, configuration, manifest, review)
    assert collect(store, configuration, manifest, review, transport=transport) == 1
    result = report(store, review, manifest)
    assert result["complete"] and result["unique_games"] == 101
    assert len(calls) == 2 and calls[1] > calls[0]
    assert not store.deliveries


def test_rate_limit_and_changed_settings_remain_incomplete():
    store, configuration, manifest, review = setup_scope()
    collect(store, configuration, manifest, review,
        transport=httpx.MockTransport(lambda request: httpx.Response(429, headers={"Retry-After": "60"})))
    result = report(store, review, manifest)
    assert not result["complete"] and result["jobs"][0]["status"] == "retryable"
    assert result["jobs"][0]["next_attempt_at"] >= FakeClock().now() + 60
    review.configuration = lambda *args: configuration.model_copy(update={"revision": 2})
    with pytest.raises(ValueError, match="historical_saved_configuration_changed"):
        collect(store, configuration, manifest, review)


def test_chesscom_historical_months_replay_original_metadata_without_http(monkeypatch):
    store, original, _, _ = setup_scope()
    configuration = original.model_copy(update={"provider": "chesscom"})
    manifest = new_manifest("fixture", (configuration,), now=datetime(2026, 10, 30, tzinfo=timezone.utc))
    review = SimpleNamespace(configuration=lambda *args: configuration,
        deliveries=lambda key: tuple(d for d in store.deliveries.values() if d.source_key == key))
    requested = []
    def http(request):
        requested.append(request.url.path)
        base = "https://api.chess.com/pub/player/learner/games/"
        if request.url.path.endswith("archives"):
            return httpx.Response(200, json={"archives": [base + f"2026/{month}" for month in ("07", "08", "09", "10")]})
        month = request.url.path[-2:]
        return httpx.Response(200, json={"games": [dict(uuid="month-"+month,
            url="https://www.chess.com/game/live/123", pgn=rows()[0]["pgn"], rules="chess",
            end_time=int(datetime(2026, int(month), 31 if month == "07" else 1, tzinfo=timezone.utc).timestamp()),
            time_class="rapid", time_control="600", rated=True)]})
    assert collect(store, configuration, manifest, review, transport=httpx.MockTransport(http)) == 4
    assert report(store, review, manifest)["unique_games"] == 4
    assert {path[-2:] for path in requested if not path.endswith("archives")} == {"07", "08", "09", "10"}
    page = RetainedPages(store, manifest.job(configuration)).fetch_page(account="learner", window=manifest.window)
    assert page.records[0].source_metadata
    monkeypatch.setattr(httpx.AsyncClient, "__init__", lambda *a, **kw: pytest.fail("offline replay attempted HTTP"))
    compare(store, configuration, manifest, review)
    assert report(store, review, manifest, compare=True)["selections"] == {"own_actionable": 4}


def test_missing_scope_retains_games_and_can_be_rechecked():
    store, configuration, manifest, review = setup_scope()
    collect(store, configuration, manifest, review)
    compare(store, configuration, manifest, review, snapshots=DeferredSnapshots())
    assert report(store, review, manifest, compare=True)["unresolved_games"] == 1
    assert not store.deliveries
    compare(store, configuration, manifest, review)
    assert report(store, review, manifest, compare=True)["selections"] == {"own_actionable": 1}
    assert len(store.deliveries) == 1


def test_historical_collection_preserves_existing_game_comparison_and_delivery():
    store, configuration, manifest, review = setup_scope()
    collect(store, configuration, manifest, review)
    compare(store, configuration, manifest, review)
    key, original = next(iter(store.sources.items()))
    delivery, = store.deliveries.values()
    assert original.first_departure.status == "own_actionable"
    other = new_manifest("fixture", (configuration,), now=datetime(2026, 10, 30, tzinfo=timezone.utc))
    collect(store, configuration, other, review)
    current = store.sources[key]
    assert current.model_dump(exclude={"version"}) == original.model_dump(exclude={"version"})
    assert store.delivery(delivery.operation_id) == delivery


def test_historical_collection_still_quarantines_changed_existing_game():
    store, configuration, manifest, review = setup_scope()
    collect(store, configuration, manifest, review)
    compare(store, configuration, manifest, review)
    key, original = next(iter(store.sources.items()))
    other = new_manifest("fixture", (configuration,), now=datetime(2026, 10, 30, tzinfo=timezone.utc))
    collect(store, configuration, other, review, records=rows(("e4",)))
    assert store.sources[key].quarantined
    assert store.sources[key].normalized == original.normalized
    assert any(item.control is not None for item in store.deliveries.values())
    assert not report(store, review, other)["complete"]


@pytest.mark.parametrize("lose_ack", [False, True])
def test_qualified_historical_acceptance_is_once_and_pending_is_not_issuance(lose_ack):
    from test_product_delivery_qualification import QualifiedReceiver
    from app.games.feedback.qualification import qualify_delivery
    from app.games.feedback.first_boundary import select_first_boundary
    store, configuration, manifest, review = setup_scope()
    collect(store, configuration, manifest, review)
    clock = FakeClock()
    class QualifiedIntake(Intake, QualifiedReceiver):
        def __init__(self):
            QualifiedReceiver.__init__(self, lose_ack=lose_ack)
        def prepare(self, delivery):
            return qualify_delivery(delivery, source=store.source(delivery.source_key),
                configuration=configuration, snapshots=(snapshot(introduction="unintroduced"),),
                determined_at=clock.utc_now(), retry_at=clock.now()+30,
                select_first_boundary=select_first_boundary)
    intake = QualifiedIntake()
    compare(store, configuration, manifest, review, intake=intake, clock=clock)
    first_command = next(iter(store.deliveries.values())).command
    if lose_ack:
        assert not report(store, review, manifest, compare=True)["complete"]
        clock.value += 60
        compare(store, configuration, manifest, review, intake=intake, clock=clock)
    result = report(store, review, manifest, compare=True)
    assert result["complete"] and result["receipt_admission_statuses"] == {"pending": 1}
    assert result["study_progress_current"] is False
    assert len(intake.receipts) == 1
    assert all(command == first_command for command, _ in intake.calls)
    assert compare(store, configuration, manifest, review, intake=intake, clock=clock) == 0


def test_scoped_jobs_do_not_claim_other_work_or_deliver_another_source():
    store, configuration, manifest, review = setup_scope()
    other = manifest.job(configuration).model_copy(update={"job_id": "unrelated"})
    store.enqueue(other)
    collect(store, configuration, manifest, review)
    assert store.load_job("unrelated").status == "queued"
    assert manifest.job(configuration).purpose == "history"
    assert manifest.job(configuration).stream == "manual"
    retained = RetainedPages(store, manifest.job(configuration))
    with pytest.raises(ValueError, match="historical_replay_scope_mismatch"):
        retained.fetch_page(account="another", window=manifest.window)


def test_foreground_admission_defers_before_creating_jobs():
    from app.services.redis_admission_gate import BackgroundAdmissionDeferred
    store, configuration, manifest, review = setup_scope()
    def deferred():
        raise BackgroundAdmissionDeferred("synthetic_foreground")
    with pytest.raises(BackgroundAdmissionDeferred):
        run_history(manifest, store=store, review=review, clock=FakeClock(), admission=deferred,
            stopped=Event(), runner_factory=lambda _: SimpleNamespace(close=lambda: None))
    assert not store.jobs


def test_native_historical_import_saved_page_replay_and_checkpoint_isolation(owned_postgres):
    from tempo_legacy_database.adapters.games.product_sources import PostgresCanonicalGamesStore
    from tempo_legacy_database.adapters.games.product_review import PostgresReviewStore
    @contextmanager
    def connections():
        with owned_postgres() as database:
            yield database.raw
    store = PostgresCanonicalGamesStore(connections)
    _, configuration, manifest, _ = setup_scope()
    review = PostgresReviewStore(connections)
    review.configure(configuration, 0)
    with connections() as database:
        before = tuple(database.execute("SELECT (SELECT count(*) FROM reviews), (SELECT count(*) FROM study_game_feedback)").fetchone())
    assert collect(store, configuration, manifest, review, records=rows(("d4", "e4 c5 2. d4"))) == 1
    assert report(store, review, manifest)["complete"]
    with connections() as database:
        assert database.execute("SELECT count(*) FROM imported_games").fetchone()[0] == 2
        assert database.execute("SELECT count(*) FROM product_feedback_deliveries").fetchone()[0] == 0
        # Automatic collectors cannot acquire historical import/comparison jobs.
        store.enqueue(manifest.job(configuration, compare=True))
        assert store.claim_job(FakeClock().now(), FakeClock().now()+120) is None
        assert database.execute("SELECT next_run_at FROM product_feedback_configuration WHERE workspace_id='fixture'").fetchone()[0] == 0
    compare(store, configuration, manifest, review, maximum_units=1)
    restored = PostgresCanonicalGamesStore(connections)
    compare(restored, configuration, manifest, review)
    result = report(restored, review, manifest, compare=True)
    assert result["selections"] == {"own_actionable": 1, "opponent_gap": 1}
    assert result["delivery_states"] == {"pending": 1}
    from app.games.feedback.interfaces import stable_id
    source_key = stable_id("fixture", "lichess", "game-0")
    original = restored.source(source_key)
    repeated = new_manifest("fixture", (configuration,), now=datetime(2026, 10, 30, tzinfo=timezone.utc))
    collect(restored, configuration, repeated, review, records=rows(("d4", "e4 c5 2. d4")))
    assert restored.source(source_key).model_dump(exclude={"version"}) == original.model_dump(exclude={"version"})
    with connections() as database:
        assert tuple(database.execute("SELECT (SELECT count(*) FROM reviews), (SELECT count(*) FROM study_game_feedback)").fetchone()) == before
        assert database.execute("SELECT count(*) FROM product_feedback_sync_requests").fetchone()[0] == 0
    # An explicit Refresh now still starts from saved seed, never this backfill.
    from app.games.feedback.refresh import refresh_job
    from app.product_composition.game_review import configured_job
    refreshed, _ = review.enqueue_refresh("fixture", "lichess", "after-historical",
        lambda current, checkpoint: refresh_job(current, checkpoint, FakeClock().now(),
            "after-historical", configured_job))
    assert datetime.fromisoformat(refreshed.spec.window.start_at) == datetime.fromisoformat(configuration.window.start_at)
    # Deliberately ambiguous stored pages fail closed.
    with connections() as database:
        database.execute("""INSERT INTO product_feedback_pages(job_id,page_key,input_continuation,page_json)
            SELECT job_id,'conflicting-retained-page',input_continuation,page_json
            FROM product_feedback_pages WHERE job_id=%s LIMIT 1""", (manifest.job(configuration).job_id,))
    with pytest.raises(ValueError, match="historical_page_missing_or_ambiguous"):
        restored.retained_page(manifest.job(configuration).job_id, None)
