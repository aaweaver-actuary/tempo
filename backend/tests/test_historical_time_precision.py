"""Legacy Lichess UTC-second and provider-millisecond source identity."""
from types import SimpleNamespace
import hashlib
import json
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from threading import Event
from uuid import uuid4
import pytest

from tempo_legacy_database.adapters.games.product_sources import legacy_lichess_precision_matches


def source(*, pgn_time="16:19:35", played_at="2026-08-02T16:19:35.810+00:00", precision="millisecond"):
    return SimpleNamespace(pgn=f'[UTCDate "2026.08.02"]\n[UTCTime "{pgn_time}"]\n\n1. e4 1-0',
        played_at=played_at, time_precision=precision)


def test_legacy_lichess_second_is_proven_by_original_pgn():
    assert legacy_lichess_precision_matches(source(), "2026-08-02T16:19:35+00:00")


@pytest.mark.parametrize("record,stored", [
    (source(pgn_time="16:19:34"), "2026-08-02T16:19:35+00:00"),
    (source(), "2026-08-02T16:19:34+00:00"),
    (source(), "2026-08-02T16:19:35.100+00:00"),
    (source(precision="unknown"), "2026-08-02T16:19:35+00:00"),
    (source(played_at="2026-08-02T16:19:36.001+00:00"), "2026-08-02T16:19:35+00:00"),
])
def test_unproved_or_different_occurrence_remains_conflict(record, stored):
    assert not legacy_lichess_precision_matches(record, stored)


def test_existing_canonical_utc_second_binds_precise_provider_game_without_rewriting(monkeypatch):
    from test_product_source_identity import inputs, raw_library, bound_store, workflow
    from app.games.providers.lichess_acquisition import LichessGamePage
    from app.games.feedback.ports import PageCommit
    original, request, snapshot = inputs(precision="millisecond", played_at="2026-10-20T12:00:00.810+00:00")
    pgn = original.pgn.replace('[Result "1-0"]', '[UTCDate "2026.10.20"]\n[UTCTime "12:00:00"]\n[Result "1-0"]')
    original = original.model_copy(update={"pgn": pgn, "source_digest": hashlib.sha256(pgn.encode()).hexdigest()})
    row = raw_library(original, request, played_at="2026-10-20T12:00:00+00:00")
    store, state = bound_store(monkeypatch, row)
    h = workflow(original, request, snapshot, store)
    page = LichessGamePage(provider="lichess", account=request.account, window=request.window,
        records=(original,), rejections=(), continuation=None, complete=True, saturated=False)
    h.coordinator.providers["lichess"] = SimpleNamespace(fetch_page=lambda **kwargs: page)
    h.memory.enqueue(request)
    assert h.runner.run_once() == "page"
    assert h.memory.load_job(request.job_id).status == "complete"
    captured, = h.memory.captures.values()
    assert captured.record.played_at == original.played_at
    assert captured.normalized.played_at == row["played_at"]
    assert captured.normalized.game_id == row["id"]
    assert not next(iter(h.memory.sources.values())).quarantined
    store.commit_page(PageCommit(job=h.memory.load_job(request.job_id),
        sources=tuple(h.memory.sources.values()), captures=(captured,), deliveries=tuple(h.memory.deliveries.values())))
    assert state.row == row and not state.writes


from test_product_game_lapse_postgres import owned_postgres


@pytest.mark.parametrize("blocker", ["none", "delivery", "write_failure", "configuration"])
def test_native_precision_recovery_preserves_quarantine_evidence_and_replays_saved_page(owned_postgres, monkeypatch, blocker):
    import httpx
    from app.games.feedback.interfaces import stable_id
    from app.product_composition.game_history_scripts import recover_legacy_precision, report, new_manifest
    from app.product_composition.game_sync import ProviderAcquisition
    from app.product_composition.game_history_scripts import configuration_policy
    from tempo_legacy_database.adapters.games.product_sources import PostgresCanonicalGamesStore, _prepare, _values
    from tempo_legacy_database.adapters.games.product_review import PostgresReviewStore
    from tempo_legacy_database.adapters.games.sync.records import insert_imported_game
    from test_game_history_scripts import setup_scope, rows, collect
    @contextmanager
    def connections():
        with owned_postgres() as database:
            yield database.raw
    store = PostgresCanonicalGamesStore(connections)
    _, configuration, manifest, _ = setup_scope()
    configuration = configuration.model_copy(update={"workspace_id":"fixture-"+uuid4().hex})
    manifest = new_manifest(configuration.workspace_id, (configuration,), now=datetime(2026,10,30,tzinfo=timezone.utc))
    review = PostgresReviewStore(connections)
    review.configure(configuration, 0)
    spec = manifest.job(configuration)
    data = rows()[0]
    data["id"] = "game-" + uuid4().hex
    data["createdAt"] += 810
    moment = datetime.fromtimestamp(data["createdAt"] / 1000, timezone.utc)
    data["pgn"] = data["pgn"].replace('[Result "1-0"]',
        f'[UTCDate "{moment:%Y.%m.%d}"]\n[UTCTime "{moment:%H:%M:%S}"]\n[Result "1-0"]')
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=json.dumps(data),
        headers={"Content-Type":"application/x-ndjson"}))
    page = ProviderAcquisition(configuration_policy(configuration), transport_factory=lambda: transport).fetch_page(
        account=spec.account, window=spec.window)
    record, = page.records
    second = moment.replace(microsecond=0).isoformat()
    legacy = _prepare(record.model_copy(update={"played_at":second}), spec)
    with owned_postgres() as database:
        insert_imported_game(database, "lichess:"+record.source_game_id, "lichess", _values(legacy))
    def old_binding(*args, **kwargs):
        raise ValueError("canonical_imported_source_conflict")
    with monkeypatch.context() as patch:
        patch.setattr(store, "bind_source", old_binding)
        assert collect(store, configuration, manifest, review, records=[data]) == 1
    job = store.load_job(spec.job_id)
    assert job.status == "quarantined" and job.error == "unresolved_page_boundary"
    key = stable_id(spec.workspace_id,spec.provider,record.source_game_id)
    before = store.source(key)
    assert before.quarantined and before.version == 1
    with connections() as database:
        saved = database.execute("SELECT payload::text FROM product_feedback_captures WHERE source_key=%s", (key,)).fetchone()[0]
        if blocker == "delivery":
            database.execute("""INSERT INTO product_feedback_deliveries
                (operation_id,source_key,effect_key,command_kind,state,next_attempt_at,payload)
                VALUES('preexisting',%s,'existing','encounter','pending',0,'{}'::jsonb)""", (key,))
        if blocker == "write_failure":
            database.execute("""CREATE FUNCTION reject_precision_update() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION 'precision_update_failure'; END $$""")
            database.execute("""CREATE TRIGGER reject_precision_update BEFORE UPDATE ON product_feedback_sources
                FOR EACH ROW EXECUTE FUNCTION reject_precision_update()""")
    if blocker == "delivery":
        with pytest.raises(ValueError, match="history_precision_delivery_exists"):
            recover_legacy_precision(manifest, store=store, review=review, admission=nullcontext, stopped=Event())
        assert store.source(key) == before
        assert store.load_job(spec.job_id).status == "quarantined"
        return
    if blocker == "configuration":
        from app.product_composition.game_sync import WorkflowNormalizer
        review.configure(configuration.model_copy(update={"revision":2}), 1)
        with pytest.raises(ValueError, match="history_precision_saved_scope_changed"):
            store.recover_initial_history_precision(spec=spec, record=record,
                verified=WorkflowNormalizer().normalize(record,spec), configuration=configuration)
        assert store.source(key) == before
        assert store.load_job(spec.job_id).status == "quarantined"
        return
    if blocker == "write_failure":
        import psycopg
        try:
            with pytest.raises(psycopg.errors.RaiseException, match="precision_update_failure"):
                recover_legacy_precision(manifest, store=store, review=review, admission=nullcontext, stopped=Event())
            assert store.source(key) == before
            assert store.load_job(spec.job_id).status == "quarantined"
            with connections() as database:
                assert database.execute("SELECT count(*) FROM product_feedback_captures WHERE source_key=%s",(key,)).fetchone()[0] == 1
        finally:
            with connections() as database:
                database.execute("DROP TRIGGER reject_precision_update ON product_feedback_sources")
                database.execute("DROP FUNCTION reject_precision_update()")
        return
    result = recover_legacy_precision(manifest, store=store, review=review, admission=nullcontext, stopped=Event())
    assert result == {"recovered":1, "page_records":1, "prepared":True, "interrupted":False}
    recovered = store.source(key)
    assert not recovered.quarantined and recovered.normalized.played_at == second
    assert recovered.original.played_at == record.played_at
    with connections() as database:
        captures = database.execute("SELECT payload::text FROM product_feedback_captures WHERE source_key=%s",(key,)).fetchall()
        assert len(captures) == 2 and any(item[0] == saved for item in captures)
        assert database.execute("SELECT played_at FROM imported_games WHERE id=%s", ("lichess:"+record.source_game_id,)).fetchone()[0] == second
    denied = httpx.MockTransport(lambda request: pytest.fail("recovery fetched provider again"))
    assert collect(store, configuration, manifest, review, transport=denied) == 1
    assert report(store, review, manifest)["complete"]
    assert store.load_job(spec.job_id).pages_committed == 2
    with connections() as database:
        assert database.execute("SELECT count(*) FROM product_feedback_pages WHERE job_id=%s",(spec.job_id,)).fetchone()[0] == 1


def test_native_one_hundred_record_page_recovers_only_proved_conflicts(owned_postgres, monkeypatch):
    import httpx
    from app.games.feedback.interfaces import stable_id
    from app.product_composition.game_history_scripts import recover_legacy_precision, new_manifest, configuration_policy
    from app.product_composition.game_sync import ProviderAcquisition
    from tempo_legacy_database.adapters.games.product_sources import PostgresCanonicalGamesStore, _prepare, _values
    from tempo_legacy_database.adapters.games.product_review import PostgresReviewStore
    from tempo_legacy_database.adapters.games.sync.records import insert_imported_game
    from test_game_history_scripts import setup_scope, rows, collect
    @contextmanager
    def connections():
        with owned_postgres() as database:
            # Match the deployed dedicated Games connection's transaction cap.
            database.raw.execute("SET LOCAL transaction_timeout = '250ms'")
            yield database.raw
    store = PostgresCanonicalGamesStore(connections)
    _, configuration, _, _ = setup_scope()
    configuration = configuration.model_copy(update={"workspace_id":"fixture-"+uuid4().hex})
    manifest = new_manifest(configuration.workspace_id, (configuration,), now=datetime(2026,10,30,tzinfo=timezone.utc))
    review = PostgresReviewStore(connections)
    review.configure(configuration, 0)
    spec = manifest.job(configuration)
    data = rows(tuple("d4" for _ in range(100)))
    for item in data:
        item["id"] = "game-" + uuid4().hex
        item["createdAt"] += 810
        moment = datetime.fromtimestamp(item["createdAt"] / 1000, timezone.utc)
        item["pgn"] = item["pgn"].replace('[Result "1-0"]',
            f'[UTCDate "{moment:%Y.%m.%d}"]\n[UTCTime "{moment:%H:%M:%S}"]\n[Result "1-0"]')
    payload = "\n".join(json.dumps(item) for item in data)
    transport = httpx.MockTransport(lambda request: httpx.Response(200,content=payload,
        headers={"Content-Type":"application/x-ndjson"}))
    page = ProviderAcquisition(configuration_policy(configuration),transport_factory=lambda:transport).fetch_page(
        account=spec.account,window=spec.window)
    assert len(page.records) == 100 and page.continuation
    conflicts = {record.source_game_id for record in page.records[:95]}
    with owned_postgres() as database:
        for record in page.records[:95]:
            second = datetime.fromisoformat(record.played_at).replace(microsecond=0).isoformat()
            legacy = _prepare(record.model_copy(update={"played_at":second}),spec)
            insert_imported_game(database,"lichess:"+record.source_game_id,"lichess",_values(legacy))
    original_binding = store.bind_source
    def old_binding(game, *, record, spec):
        if record.source_game_id in conflicts:
            raise ValueError("canonical_imported_source_conflict")
        return original_binding(game,record=record,spec=spec)
    with monkeypatch.context() as patch:
        patch.setattr(store,"bind_source",old_binding)
        assert collect(store,configuration,manifest,review,transport=transport,maximum_units=1) == 1
    assert store.load_job(spec.job_id).status == "quarantined"
    from app.services.redis_admission_gate import BackgroundAdmissionDeferred
    admitted = 0
    @contextmanager
    def interrupted_admission():
        nonlocal admitted
        admitted += 1
        if admitted == 4:
            raise BackgroundAdmissionDeferred("foreground")
        yield
    with pytest.raises(BackgroundAdmissionDeferred):
        recover_legacy_precision(manifest,store=store,review=review,admission=interrupted_admission,stopped=Event())
    assert store.load_job(spec.job_id).status == "quarantined"
    assert sum(not store.source(stable_id(spec.workspace_id,spec.provider,r.source_game_id)).quarantined
        for r in page.records[:95]) == 3
    result = recover_legacy_precision(manifest,store=store,review=review,admission=nullcontext,stopped=Event())
    assert result["recovered"] == 92 and result["page_records"] == 100 and result["prepared"]
    for record in page.records:
        state = store.source(stable_id(spec.workspace_id,spec.provider,record.source_game_id))
        assert not state.quarantined
    denied = httpx.MockTransport(lambda request: pytest.fail("saved page replay fetched provider"))
    assert collect(store,configuration,manifest,review,transport=denied,maximum_units=1) == 1
    job = store.load_job(spec.job_id)
    assert job.status == "queued" and job.continuation == page.continuation and job.pages_committed == 2
    with connections() as database:
        assert database.execute("SELECT count(*) FROM product_feedback_pages WHERE job_id=%s",(spec.job_id,)).fetchone()[0] == 1
        assert database.execute("SELECT count(*) FROM product_feedback_deliveries WHERE source_key=ANY(%s)",
            ([stable_id(spec.workspace_id,spec.provider,r.source_game_id) for r in page.records],)).fetchone()[0] == 0
