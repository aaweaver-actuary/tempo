"""Cheap semantic scheduling/wiring checks; no network, database or browser."""
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Event

import pytest

from app.games.feedback.continuous import ContinuousProviderState, ContinuousSync, ForwardWindow, stream_prefix
from app.games.feedback.ports import JobSpec, JobState
from app.games.feedback.review import ReviewUnavailable, SyncConfiguration

START = 1793059200


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def configuration(provider="lichess", revision=1, interval=60):
    return SyncConfiguration(workspace_id="legacy", provider=provider, account="ExplicitAccount",
        window={"start_at": stamp(START), "end_at": stamp(START+3600), "precision": "second"},
        repertoire_ids=("approved",), speeds=("rapid",), rated=True,
        interval_seconds=interval, revision=revision)


def build(config, operation):
    return JobSpec(job_id=operation, workspace_id=config.workspace_id, provider=config.provider,
        account=config.account.casefold(), window=config.window, repertoire_ids=config.repertoire_ids,
        filters=(("explicit", "unchanged"),))


def test_forward_initial_and_restart_identity_preserve_explicit_scope():
    policy = ForwardWindow(300)
    first = policy.next_job(configuration(), None, now=START+5000, build_job=build)
    repeated = policy.next_job(configuration(), None, now=START+5100, build_job=build)
    assert first == repeated and first.window == configuration().window
    assert first.account == "explicitaccount" and first.repertoire_ids == ("approved",)
    completed = JobState(spec=first, status="complete", window_complete=True)
    resumed = policy.next_job(configuration(), completed, now=START+100000, build_job=build)
    assert resumed.window.start_at == stamp(START+3300)
    assert resumed.window.end_at == stamp(START+6900)
    assert resumed.filters == first.filters


@pytest.mark.parametrize("status", ["queued", "running", "retryable", "failed", "quarantined"])
def test_unfinished_or_held_window_keeps_original_checkpoint(status):
    policy = ForwardWindow(300)
    first = policy.next_job(configuration(), None, now=START+5000, build_job=build)
    previous = JobState(spec=first, status=status, continuation="exact-cursor", next_attempt_at=START+99999)
    before = previous.model_dump()
    assert policy.next_job(configuration(), previous, now=START+200000, build_job=build) is None
    assert previous.model_dump() == before


def test_complete_window_waits_for_new_time_and_disabled_recurrence_stays_disabled():
    policy = ForwardWindow(300)
    first = policy.next_job(configuration(), None, now=START+3600, build_job=build)
    previous = JobState(spec=first, status="complete", window_complete=True)
    assert policy.next_job(configuration(), previous, now=START+3600, build_job=build) is None
    assert policy.next_job(configuration(interval=None), None, now=START+5000, build_job=build) is None


def test_changed_previous_scope_is_refused_instead_of_inferred():
    policy = ForwardWindow(300)
    first = policy.next_job(configuration(), None, now=START+5000, build_job=build)
    previous = JobState(spec=first.model_copy(update={"account": "another-account"}), status="complete", window_complete=True)
    with pytest.raises(ReviewUnavailable, match="scope_changed"):
        policy.next_job(configuration(), previous, now=START+10000, build_job=build)


class ScheduleStore:
    """Typed scheduling facts over the existing job fake; no policy in the fake."""
    def __init__(self, configurations=None):
        self.configurations = configurations or (configuration("lichess"), configuration("chesscom"))
        self.jobs, self.enqueued, self.next_run = {}, [], {}

    def stream(self, config, purpose):
        jobs = [j for j in self.jobs.values() if j.spec.provider == config.provider
                and j.spec.job_id.startswith(stream_prefix(config, purpose))]
        unfinished = [j for j in jobs if j.status != "complete" or not j.window_complete]
        return unfinished[0] if unfinished else max(jobs, key=lambda j: j.spec.window.end_at, default=None)

    def continuous_state(self, workspace, now):
        return tuple(ContinuousProviderState(c, self.next_run.get(c.provider, 0) <= now,
            self.stream(c, "forward"), self.stream(c, "reconciliation"))
            for c in self.configurations if c.interval_seconds is not None)

    def enqueue_continuous(self, config, job, now, *, purpose, expected_previous):
        from app.games.feedback.ports import ConcurrentUpdate
        if self.stream(config, purpose) != expected_previous:
            raise ConcurrentUpdate()
        self.jobs[job.job_id] = JobState(spec=job)
        self.enqueued.append(job)
        if purpose == "forward":
            self.next_run[config.provider] = now + config.interval_seconds
        return self.jobs[job.job_id]


def schedule(store, clock, interval=None):
    return ContinuousSync(workspace="legacy", store=store, policy=ForwardWindow(300), build_job=build,
        now=lambda: clock[0], reconciliation_interval_seconds=interval)


def finish(store, job, now):
    store.jobs[job.job_id] = store.jobs[job.job_id].model_copy(update={
        "status": "complete", "window_complete": True, "last_checkpoint_at": stamp(now)})


def test_one_held_provider_does_not_starve_another():
    store, clock = ScheduleStore(), [START+5000]
    held = ForwardWindow(300).next_job(configuration(), None, now=clock[0], build_job=build)
    store.jobs[held.job_id] = JobState(spec=held, status="retryable", continuation="held-cursor")
    scheduler = schedule(store, clock)
    assert scheduler.enqueue_due() and [j.provider for j in store.enqueued] == ["chesscom"]
    assert not scheduler.enqueue_due() and store.jobs[held.job_id].continuation == "held-cursor"


def test_manual_backfill_never_resets_forward_progress_and_reconciliation_is_independent():
    store, clock = ScheduleStore((configuration(),)), [START+5000]
    scheduler = schedule(store, clock, 600)
    assert scheduler.enqueue_due()
    forward, reconciliation = store.enqueued
    assert forward.job_id.startswith("following:") and reconciliation.job_id.startswith("reconciling:")
    assert reconciliation.window.start_at == stamp(START)
    assert reconciliation.window.end_at == stamp(clock[0])
    finish(store, forward, clock[0])
    manual = build(configuration().model_copy(update={"window": {
        "start_at": stamp(START-86400), "end_at": stamp(START), "precision": "second"}}), "manual-backfill")
    # Manual JobSpec may use an arbitrary historical window without altering configuration.
    store.jobs[manual.job_id] = JobState(spec=manual, status="failed", continuation="manual-cursor")
    clock[0] += 60
    assert scheduler.enqueue_due()
    next_forward = store.enqueued[-1]
    assert next_forward.window.start_at == stamp(START+3300)
    assert next_forward.window.end_at == stamp(START+5040)
    assert store.jobs[manual.job_id].continuation == "manual-cursor"
    assert len([j for j in store.enqueued if j.job_id.startswith("reconciling:")]) == 1


def test_reconciliation_restart_freezes_scope_cursor_and_waits_from_completion():
    store, clock = ScheduleStore((configuration(),)), [START+5000]
    assert schedule(store, clock, 600).enqueue_due()
    job = store.enqueued[-1]
    store.jobs[job.job_id] = JobState(spec=job, status="retryable", continuation="page-two",
                                   next_attempt_at=clock[0]+90)
    clock[0] += 2000
    before = store.jobs[job.job_id]
    assert not schedule(store, clock, 600).enqueue_due()
    assert store.jobs[job.job_id] == before
    finish(store, job, clock[0])
    clock[0] += 599
    assert not schedule(store, clock, 600).enqueue_due()
    clock[0] += 1
    forward_due = store.next_run.copy()
    assert schedule(store, clock, 600).enqueue_due()
    assert store.enqueued[-1].window.end_at == stamp(clock[0])
    assert store.next_run == forward_due


@pytest.mark.parametrize("value", [True, 59, 0, -1, 60.0])
def test_reconciliation_requires_explicit_integer_cadence(value):
    with pytest.raises(ValueError, match="reconciliation_interval"):
        schedule(ScheduleStore(), [START], value)


def test_reconciliation_default_remains_disabled_and_stale_enqueue_retries_next_pass():
    from app.games.feedback.ports import ConcurrentUpdate
    store = ScheduleStore()
    enqueue = store.enqueue_continuous
    def racing(config, job, now, **kwargs):
        if config.provider == "lichess":
            raise ConcurrentUpdate("another_scheduler_won")
        return enqueue(config, job, now, **kwargs)
    store.enqueue_continuous = racing
    assert schedule(store, [START+5000]).enqueue_due()
    assert [j.provider for j in store.enqueued] == ["chesscom"]
    assert all(j.job_id.startswith("following:") for j in store.enqueued)


def test_current_worker_requires_qualified_receiver_before_any_source_read():
    from app.product_composition.game_sync_current import create_current_game_sync
    def forbidden():
        pytest.fail("source read before receiving identity contract")
    with pytest.raises(ReviewUnavailable, match="qualified_provider_encounter_receiver_required"):
        create_current_game_sync(workspace="legacy", connections=forbidden)


def test_publication_reader_uses_published_owner_not_authored_counter(monkeypatch):
    from app.product_composition import game_sync_current as composition
    marker = object()
    @contextmanager
    def connections():
        yield "borrowed-reader"
    def published(database, repertoire):
        assert (database, repertoire) == ("borrowed-reader", "approved")
        return marker
    monkeypatch.setattr(composition, "current_published_snapshot", published)
    assert composition.CurrentRepertoireSnapshots(connections).snapshot("approved") is marker


def test_reader_profile_cannot_be_reused_as_writer():
    from tempo_legacy_database.adapters.games.product_review import DedicatedGamesConnections
    with pytest.raises(ReviewUnavailable, match="writer_profile_required"):
        with DedicatedGamesConnections("unused-reader", read_only=True).writer():
            pytest.fail("reader became a writer")


def test_actual_admission_deferral_claims_no_games_job():
    from app.product_composition.game_sync import GameSyncRuntime
    from app.services.redis_admission_gate import BackgroundAdmissionDeferred
    class Runner:
        def run_once(self):
            pytest.fail("denied admission claimed work")
    @contextmanager
    def denied():
        raise BackgroundAdmissionDeferred("genuine foreground")
        yield
    assert GameSyncRuntime(Runner(), Event(), admission=denied).run_once() == "deferred"


def test_disabled_schedule_does_not_enqueue_even_when_called_directly(monkeypatch):
    from app.product_composition import game_sync_current as composition
    from app.services import study_priority
    monkeypatch.delenv("TEMPO_PRODUCT_GAME_SYNC_ENABLED", raising=False)
    monkeypatch.setattr(study_priority,"control_snapshot",lambda:dict(available=True,active=False,study_only_mode=True))
    class Schedule:
        def enqueue_due(self):
            pytest.fail("disabled optional process enqueued a job")
    assert composition.CurrentSyncSchedule(Schedule()).enqueue_due() is False


def test_single_configured_provider_constructs_without_http_or_other_provider():
    from app.product_composition.game_sync import AcquisitionPolicy,create_game_sync
    class Clock:
        def now(self):return START
    def forbidden():pytest.fail("provider client opened during construction")
    runtime=create_game_sync(policies=(AcquisitionPolicy(provider="lichess",speeds=("rapid",),
        rated=True,source_revision=1),),store=object(),snapshots=object(),learning=object(),
        clock=Clock(),transport_factory=forbidden,encounter_feedback=True)
    assert set(runtime.runner.coordinator.providers)=={"lichess"}
    assert runtime.runner.coordinator.encounter_feedback
    runtime.close()


@pytest.mark.parametrize("policy",[
    dict(available=False,active=True,study_only_mode=None),
    dict(available=True,active=True,study_only_mode=True),
])
def test_current_unknown_or_foreground_controls_prevent_optional_unit(monkeypatch,policy):
    from app.product_composition.game_sync_current import current_sync_admission
    from app.services import study_priority
    from app.services.redis_admission_gate import BackgroundAdmissionDeferred
    monkeypatch.setenv("TEMPO_PRODUCT_GAME_SYNC_ENABLED","1")
    monkeypatch.setattr(study_priority,"control_snapshot",lambda:policy)
    with pytest.raises(BackgroundAdmissionDeferred):
        with current_sync_admission():pytest.fail("control admitted optional work")


# Real acquisition adapters with synthetic HTTP. No provider requests or Study owner.
PGN = '[White "ExplicitAccount"]\n[Black "Opponent"]\n[Result "1-0"]\n\n1. e4 e5 2. Nf3 Nc6 1-0\n'


def archive_row(identity, month="10", **changes):
    row = dict(uuid=identity, url="https://www.chess.com/game/live/123", pgn=PGN, rules="chess",
        end_time=int(datetime.fromisoformat(f"2026-{month}-27T00:00:00+00:00").timestamp()),
        time_class="rapid", time_control="600", rated=True)
    return row | changes


def provider_fetch(provider, handler, window, token=None, **bounds):
    import asyncio
    import httpx
    from app.games.providers.chesscom_acquisition import fetch_chesscom_page
    from app.games.providers.lichess_acquisition import fetch_lichess_page
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            common = dict(account="explicitaccount", window=window, client=client, continuation=token,
                          source_revision=1, speeds=("rapid",), **bounds)
            if provider == "lichess":
                return await fetch_lichess_page(**common, rated=True)
            return await fetch_chesscom_page(**common, rated_only=True)
    return asyncio.run(run())


def archive_http(months, responses=None):
    import httpx
    def handle(request):
        if responses is not None:
            responses.append(str(request.url))
        if request.url.path.endswith("archives"):
            return httpx.Response(200, json={"archives": [
                "https://api.chess.com/pub/player/explicitaccount/games/2026/"+m for m in months]})
        return httpx.Response(200, json={"games": months[request.url.path[-2:]]})
    return handle


@pytest.mark.parametrize("bounds", [{}, {"max_page_positions": 12}, {"max_page_bytes": len(PGN.encode())*3}])
def test_dense_two_month_archive_exact_ids_with_record_position_and_byte_pages(bounds):
    from app.contracts.product_v02.games import GameWindow
    window = GameWindow(start_at=stamp(START), end_at="2026-12-01T00:00:00Z", precision="second")
    months = {m: [archive_row(f"{m}-{i:04}", m) for i in range(205)] for m in ("10", "11")}
    token, identities, pages = None, [], 0
    while True:
        page = provider_fetch("chesscom", archive_http(months), window, token, **bounds)
        assert len(page.records) <= 100
        assert sum(len(r.pgn.encode()) for r in page.records) <= bounds.get("max_page_bytes", 8*1024*1024)
        assert len(page.records)*4 <= bounds.get("max_page_positions", 10000)
        identities.extend(r.source_game_id for r in page.records)
        pages += 1
        if page.complete:
            break
        assert page.continuation and page.continuation != token
        token = page.continuation  # Recreate client/function state on every page.
        assert pages < 200
    assert len(identities) == len(set(identities)) == 410
    assert set(identities) == {row["uuid"] for month in months.values() for row in month}


def test_archive_mutation_replays_month_without_mutating_record_provenance_and_v1_resumes():
    import base64
    import json
    months = {"10": [archive_row(f"source-{i:03}") for i in range(105)]}
    window = configuration().window
    first = provider_fetch("chesscom", archive_http(months), window)
    assert len(first.records) == 100
    months["10"].reverse()  # Mere array reordering does not restart the snapshot.
    reordered = provider_fetch("chesscom", archive_http(months), window, first.continuation)
    assert len(reordered.records) == 5 and reordered.complete
    months["10"].append(archive_row("late-source"))
    replay = provider_fetch("chesscom", archive_http(months), window, first.continuation)
    assert replay.records[0].source_game_id == "late-source"
    assert set(first.records) & set(replay.records)  # Exact original metadata, not a new source revision.
    token = json.loads(base64.urlsafe_b64decode(first.continuation))
    legacy = {k: v for k, v in token.items() if k in {"scope", "months", "index"}} | {"v": 1}
    resumed = provider_fetch("chesscom", archive_http(months), window,
        base64.urlsafe_b64encode(json.dumps(legacy).encode()).decode())
    assert resumed == replay
    wrong = configuration().window.model_copy(update={"end_at": stamp(START+7200)})
    from app.games.providers.chesscom_acquisition import ChesscomAcquisitionError
    with pytest.raises(ChesscomAcquisitionError, match="invalid_continuation"):
        provider_fetch("chesscom", archive_http(months), wrong, first.continuation)


@pytest.mark.parametrize("provider", ["lichess", "chesscom"])
def test_provider_429_retains_original_cursor_and_retry_after(provider):
    import httpx
    from app.games.providers.chesscom_acquisition import ChesscomAcquisitionError
    from app.games.providers.lichess_acquisition import LichessAcquisitionError
    if provider == "chesscom":
        first = provider_fetch(provider, archive_http({"10": [archive_row(str(i)) for i in range(105)]}), configuration().window)
    else:
        first = provider_fetch(provider, lichess_http(105), configuration().window)
    def limited(request):
        return httpx.Response(429, headers={"Retry-After": "17"})
    with pytest.raises((ChesscomAcquisitionError, LichessAcquisitionError)) as failed:
        provider_fetch(provider, limited, configuration().window, first.continuation)
    assert failed.value.continuation == first.continuation
    assert failed.value.retry_after == "17"


def lichess_http(count, *, available=True, same_timestamp=False):
    import json
    import httpx
    rows = [dict(id=f"late{i:04}", createdAt=START*1000+(0 if same_timestamp else i),
                 speed="rapid", rated=True, pgn=PGN) for i in range(count)] if available else []
    def handle(request):
        params = request.url.params
        selected = [r for r in rows if int(params["since"]) <= r["createdAt"] < int(params["until"])]
        selected = selected[:int(params["max"])]
        return httpx.Response(200, content="\n".join(json.dumps(r) for r in selected),
                              headers={"Content-Type": "application/x-ndjson"})
    return handle


@pytest.mark.parametrize("same_timestamp", [False, True])
@pytest.mark.parametrize("bounds", [{"max_page_positions": 12}, {"max_page_bytes": len(PGN.encode())*3}])
def test_lichess_budget_split_keeps_unconsumed_timestamp_ids(same_timestamp, bounds):
    token, identities = None, []
    for _ in range(20):
        page = provider_fetch("lichess", lichess_http(17, same_timestamp=same_timestamp),
                              configuration().window, token, **bounds)
        assert len(page.records) <= 3
        identities.extend(r.source_game_id for r in page.records)
        if page.complete:
            break
        assert page.continuation != token
        token = page.continuation
    else:
        pytest.fail("bounded pages did not finish")
    assert len(identities) == len(set(identities)) == 17


@pytest.mark.parametrize("provider", ["lichess", "chesscom"])
def test_reconciliation_finds_late_provider_visibility_after_forward_overlap_exactly_once(provider):
    from app.games.feedback.ports import JobState
    store, clock = ScheduleStore((configuration(provider),)), [START+5000]
    scheduler = schedule(store, clock, 600)
    assert scheduler.enqueue_due()
    # An unfinished Lichess game is absent from the finished export. Chess.com
    # may publish an old game's archive row later. Both initially return empty.
    absent = lichess_http(0) if provider == "lichess" else archive_http({"10": []})
    for job in store.enqueued:
        assert provider_fetch(provider, absent, job.window).complete
        finish(store, job, clock[0])
    clock[0] += 3600
    assert scheduler.enqueue_due()
    visible = lichess_http(1) if provider == "lichess" else archive_http({"10": [archive_row("late0000")]})
    forward, reconciliation = store.enqueued[-2:]
    forward_page = provider_fetch(provider, visible, forward.window)
    assert not forward_page.records
    recovered = provider_fetch(provider, visible, reconciliation.window)
    assert [r.source_game_id for r in recovered.records] == ["late0000"]
    # Exercise the real page-commit/deduplication path with the existing semantic
    # fake receiver; no Study implementation or settlement is involved.
    from test_product_feedback_jobs import harness
    h = harness()
    h.clock.value = clock[0]
    for number in range(2):
        request = reconciliation.model_copy(update={"job_id": f"replay-{number}",
            "workspace_id": "fixture", "repertoire_ids": ("white",), "filters": ()})
        h.store.enqueue(request)
        h.page[0] = recovered
        h.coordinator.providers[provider].fetch_page = lambda **_: recovered
        # The fixture comparer returns Lichess findings; adapt only that supplied fact.
        compare = h.coordinator.comparer.compare
        h.coordinator.comparer.compare = lambda *a, _compare=compare, **kw: tuple(
            f.model_copy(update={"provider": provider}) for f in _compare(*a, **kw))
        h.coordinator.capture_page(h.store.load_job(request.job_id))
    assert len(h.store.sources) == 1 and len(h.store.deliveries) == 1


@pytest.mark.parametrize("provider", ["lichess", "chesscom"])
def test_provider_unfinished_exclusion_is_distinct_from_malformed_and_resource_failure(provider):
    import httpx
    from app.games.providers.chesscom_acquisition import ChesscomAcquisitionError
    from app.games.providers.lichess_acquisition import LichessAcquisitionError
    unfinished = PGN.replace('"1-0"', '"*"').replace('Nc6 1-0', 'Nc6 *')
    if provider == "chesscom":
        handler = archive_http({"10": [archive_row("unfinished", pgn=unfinished), archive_row("invalid", pgn=PGN.replace("e4 e5", "e5 e5"))]})
    else:
        import json
        rows = [dict(id=name, createdAt=START*1000+i, rated=True, speed="rapid", pgn=pgn)
                for i, (name, pgn) in enumerate((("unfinished", unfinished), ("invalid", PGN.replace("e4 e5", "e5 e5"))))]
        handler = lambda request: httpx.Response(200, content="\n".join(json.dumps(r) for r in rows),
                                               headers={"Content-Type": "application/x-ndjson"})
    page = provider_fetch(provider, handler, configuration().window)
    assert not page.records and page.complete
    assert {r.reason for r in page.rejections} == {"unfinished_game", "invalid_pgn" if provider == "lichess" else "invalid_pgn_or_player"}
    error = LichessAcquisitionError if provider == "lichess" else ChesscomAcquisitionError
    valid = lichess_http(1) if provider == "lichess" else archive_http({"10": [archive_row("valid")]})
    with pytest.raises(error, match="record_exceeds_page_budget") as failed:
        provider_fetch(provider, valid, configuration().window, max_page_positions=1)
    assert failed.value.continuation is None
    with pytest.raises(error, match="cancelled"):
        provider_fetch(provider, valid, configuration().window, cancelled=lambda: True)
    with pytest.raises(error, match="response_limit|response_too_large"):
        provider_fetch(provider, valid, configuration().window, max_response_bytes=1)


def test_conflicting_archive_ids_are_detected_before_first_slice():
    from app.games.providers.chesscom_acquisition import ChesscomAcquisitionError
    rows = [archive_row(f"game-{i:04}") for i in range(101)]
    rows.append(archive_row("game-0000", pgn=PGN.replace('e4 e5', 'd4 d5')))
    with pytest.raises(ChesscomAcquisitionError, match="conflicting_source_id"):
        provider_fetch("chesscom", archive_http({"10": rows}), configuration().window)


def test_both_provider_forward_and_reconciliation_progress_with_midpage_429_and_restart():
    import httpx
    from types import SimpleNamespace
    from app.product_composition.game_sync import AcquisitionPolicy, ProviderAcquisition, WorkflowAcquisition
    from test_product_feedback_jobs import harness
    h = harness()
    h.clock.value = START+5000
    configurations = tuple(configuration(p).model_copy(update={"workspace_id": "fixture", "repertoire_ids": ("white",)})
                           for p in ("lichess", "chesscom"))
    schedules = ScheduleStore(configurations)
    schedules.jobs = h.store.jobs
    calls, limited = [], [False]
    lichess = lichess_http(205)
    chesscom = archive_http({"10": [archive_row(f"archive-{i:04}") for i in range(205)]})
    def http(request):
        calls.append(request)
        if request.url.host == "lichess.org":
            return httpx.Response(429, headers={"Retry-After": "17"}) if limited[0] else lichess(request)
        return chesscom(request)
    def builder(config, identity):
        filters = (("perfType", "rapid"), ("rated", "true")) if config.provider == "lichess" else (
            ("speeds", "rapid"), ("rated_only", "true"), ("variant", "standard"))
        return build(config, identity).model_copy(update={"filters": filters})
    for provider in ("lichess", "chesscom"):
        acquisition = ProviderAcquisition(AcquisitionPolicy(provider=provider, speeds=("rapid",),
            rated=True, source_revision=1), transport_factory=lambda: httpx.MockTransport(http))
        h.coordinator.providers[provider] = WorkflowAcquisition(acquisition, now=h.clock.now)
    compare = h.coordinator.comparer.compare
    h.coordinator.comparer = SimpleNamespace(compare=lambda *a, **kw: tuple(
        f.model_copy(update={"provider": kw["spec"].provider}) for f in compare(*a, **kw)))
    scheduler = ContinuousSync(workspace="fixture", store=schedules, policy=ForwardWindow(300),
        build_job=builder, now=h.clock.now, reconciliation_interval_seconds=600)
    assert scheduler.enqueue_due() and len(h.store.jobs) == 4
    # Let each stream commit a first page, then make Lichess unavailable.
    for _ in range(8):
        h.runner.run_once()
    original = {k: j.continuation for k, j in h.store.jobs.items() if j.spec.provider == "lichess"}
    assert len(original) == 2 and all(original.values())
    limited[0] = True
    for _ in range(24):
        h.runner.run_once()
    assert all(j.status == "complete" for j in h.store.jobs.values() if j.spec.provider == "chesscom")
    for key, continuation in original.items():
        job = h.store.jobs[key]
        assert job.continuation == continuation and job.status == "retryable"
        assert job.next_attempt_at == h.clock.value+17
    # A recreated runner/store view resumes the exact same cursors when due.
    from app.games.feedback.jobs import FeedbackRunner
    h.runner = FeedbackRunner(h.coordinator)
    limited[0] = False
    h.clock.value += 17
    for _ in range(24):
        h.runner.run_once()
    assert all(j.status == "complete" and j.window_complete for j in h.store.jobs.values())
    assert len(h.store.sources) == len(h.store.deliveries) == 410


@pytest.mark.parametrize("provider", ["lichess", "chesscom"])
@pytest.mark.parametrize("result", ["*", "unsupported", None])
def test_actual_adapter_result_checkpoint_and_later_reconciliation(provider, result):
    """Only explicit unfinished results advance; reconciliation sees completion."""
    import json
    import httpx
    from test_product_feedback_jobs import harness
    from app.product_composition.game_sync import AcquisitionPolicy, ProviderAcquisition, WorkflowAcquisition, WorkflowNormalizer, WorkflowComparer, sync_job
    from app.games.feedback.jobs import FeedbackRunner
    from app.contracts.product_v02.games import GameWindow
    window = GameWindow(start_at="2026-10-01T00:00:00Z", end_at="2026-11-01T00:00:00Z", precision="second")
    text = PGN.replace('[Result "1-0"]', '' if result is None else f'[Result "{result}"]')
    # Do not let the movetext supply a missing header's finished result.
    text = text.replace('Nc6 1-0', 'Nc6 *')
    current = [text]
    def handle(request):
        if provider == "chesscom":
            return archive_http({"10": [archive_row("resultcase", pgn=current[0])]})(request)
        return httpx.Response(200, content=json.dumps(dict(id="resultcase", createdAt=(START+100)*1000,
            rated=True, speed="rapid", pgn=current[0])) + "\n",
            headers={"Content-Type": "application/x-ndjson"})
    policy = AcquisitionPolicy(provider=provider, speeds=("rapid",), rated=True, source_revision=1)
    h = harness()
    h.coordinator.normalizer = WorkflowNormalizer()
    h.coordinator.comparer = WorkflowComparer()
    h.coordinator.providers[provider] = WorkflowAcquisition(ProviderAcquisition(policy,
        transport_factory=lambda: httpx.MockTransport(handle)), now=h.clock.now)
    job = sync_job(job_id="following:result", workspace_id="fixture", account="ExplicitAccount",
        window=window, repertoire_ids=("white",), policy=policy)
    h.store.enqueue(job)
    runner = FeedbackRunner(h.coordinator, delivery_enabled=False)
    assert runner.run_once() == "page"
    observed = h.store.load_job(job.job_id)
    assert observed.window_complete is (result == "*")
    assert observed.status == ("complete" if result == "*" else "quarantined")
    assert bool(observed.last_checkpoint_at) is (result == "*")
    assert observed.rejections[0].reason == ("unfinished_game" if result == "*" else
        "invalid_pgn" if provider == "lichess" else "invalid_pgn_or_player")
    current[0] = PGN
    h.store.enqueue(job.model_copy(update={"job_id": "reconciling:result"}))
    assert runner.run_once() == "page"
    assert h.store.load_job("reconciling:result").window_complete
    assert len(h.store.sources) == 1
