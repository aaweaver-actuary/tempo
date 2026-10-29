"""Cheap semantic scheduling/wiring checks; no network, database or browser."""
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Event

import pytest

from app.games.feedback.continuous import ContinuousSync, ForwardWindow
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


def test_one_held_provider_does_not_starve_another():
    class Store:
        def __init__(self):
            self.pending, self.enqueued = {}, []
        def due_configurations(self, *_):
            return (configuration("lichess"), configuration("chesscom"))
        def last_job(self, workspace, provider):
            return self.pending.get(provider)
        def enqueue(self, config, job, now):
            self.enqueued.append(job)
            self.pending[config.provider] = JobState(spec=job)
    store = Store()
    store.pending["lichess"] = JobState(spec=build(configuration(), "manual-held"),
        status="retryable", continuation="held-cursor")
    scheduler = ContinuousSync(workspace="legacy", store=store, policy=ForwardWindow(300), build_job=build, now=lambda: START+5000)
    assert scheduler.enqueue_due() and [job.provider for job in store.enqueued] == ["chesscom"]
    assert not scheduler.enqueue_due() and store.pending["lichess"].continuation == "held-cursor"


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
