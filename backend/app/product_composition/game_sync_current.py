"""Explicit optional-process assembly over current source owners.

Importing this module starts nothing. The HTTP app does not import or invoke its
launcher; saved recurrence alone cannot activate a worker.
"""
from app.games.feedback.review import ReviewUnavailable
from app.contracts.product_v02.learning import EncounterFeedbackPort
from tempo_legacy_database.adapters.authored_repertoires.product_snapshot import current_published_snapshot
from tempo_legacy_database.adapters.games.product_review import (
    DedicatedGamesConnections, PostgresReviewStore,
)
from tempo_legacy_database.adapters.games.product_sources import PostgresCanonicalGamesStore

from .game_review import SystemClock, configuration_policy, configured_job, create_game_review
from app.games.feedback.continuous import ContinuousSync, ForwardWindow
from .game_sync import create_game_sync
from .game_worker import run_optional_worker

from contextlib import contextmanager


def _require_current_sync_controls():
    import os
    from app.services.redis_admission_gate import BackgroundAdmissionDeferred
    from app.services.study_priority import control_snapshot
    policy=control_snapshot()
    if (os.environ.get("TEMPO_PRODUCT_GAME_SYNC_ENABLED")!="1"
            or policy.get("available") is not True or type(policy.get("active")) is not bool
            or type(policy.get("study_only_mode")) is not bool or policy["active"]):
        raise BackgroundAdmissionDeferred("Games sync is disabled, unavailable or waiting for foreground activity")


@contextmanager
def current_sync_admission():
    from app.services.activity_gate import activity_gate
    _require_current_sync_controls()
    with activity_gate.background_job("product_game_sync","worker",yielding=True):
        activity_gate.check_background_admission()
        yield


class CurrentSyncSchedule:
    """Do not enqueue recurring work without the same actual admission evidence."""
    def __init__(self, schedule):
        self.schedule=schedule

    def enqueue_due(self):
        from app.services.redis_admission_gate import BackgroundAdmissionDeferred
        try:
            with current_sync_admission():
                return self.schedule.enqueue_due()
        except BackgroundAdmissionDeferred:
            return False


class CurrentRepertoireSnapshots:
    def __init__(self, connections):
        self.connections = connections

    def snapshot(self, repertoire_id):
        with self.connections() as database:
            return current_published_snapshot(database, repertoire_id)


def create_current_game_sync(*, workspace, connections, clock=None,
                             learning=None, forward_window=None, transport_factory=None):
    """Build one opted-in worker using both explicitly saved provider policies.

    Comparison uses published source facts. Canonical Games publication and its
    original outbox commit before calling the injected public intake. Evidence
    acceptance does not construct or wait for the receiver's queue refresh.
    """
    if learning is None:
        # Canonical identity is joined by SourceIdentity. The concrete receiving
        # seam still needs native qualification before default CLI activation.
        # Never fall back to the older lapse-only bridge or relabel a finding.
        raise ReviewUnavailable("qualified_provider_encounter_receiver_required",503)
    clock = clock or SystemClock()
    configuration_store = PostgresReviewStore(connections)
    configurations = tuple(value for provider in ("lichess", "chesscom")
        if (value:=configuration_store.configuration(workspace,provider)) is not None)
    if not configurations:
        raise ReviewUnavailable("explicit_sync_configuration_required")
    @contextmanager
    def configured_admission():
        with current_sync_admission():
            current = tuple(value for provider in ("lichess", "chesscom")
                if (value:=configuration_store.configuration(workspace,provider)) is not None)
            if current != configurations:
                # The independently restarted process rebuilds its concrete
                # acquisition policies. Never run a newly edited filter/window
                # through clients constructed for the previous configuration.
                raise ReviewUnavailable("sync_configuration_changed_restart_worker",503)
            yield
    store = PostgresCanonicalGamesStore(connections)
    runtime = create_game_sync(policies=tuple(configuration_policy(value) for value in configurations),
        store=store, snapshots=CurrentRepertoireSnapshots(connections), learning=learning,
        clock=clock, source_identity=store,
        transport_factory=transport_factory,encounter_feedback=True,admission=configured_admission)
    review = create_game_review(workspace=workspace, connections=connections,
                               learning=learning, clock=clock, runtime=runtime)
    schedule=(ContinuousSync(workspace=workspace,store=configuration_store,policy=forward_window,
        build_job=configured_job,now=clock.now) if forward_window is not None else review)
    return runtime, CurrentSyncSchedule(schedule)


def create_current_provider_worker(*, workspace, connections,
                                   priority_intake: EncounterFeedbackPort | None = None,
                                   clock=None, forward_window=None, transport_factory=None):
    """Wire only the injected public idempotent priority intake.

    Accepted/pending is evidence acceptance, not queue availability. The root
    supplies the receiver implementation; Games never constructs a Study owner,
    borrows its writer, or refreshes/promotes its queue during delivery.
    """
    if priority_intake is None:
        raise ReviewUnavailable("decoupled_priority_intake_required",503)
    return create_current_game_sync(workspace=workspace, connections=connections,
        learning=priority_intake, clock=clock, forward_window=forward_window,
        transport_factory=transport_factory)


def current_provider_worker(*, workspace, connections, clock=None, forward_window=None,
                            transport_factory=None):
    """Concrete opt-in root: the worker alone owns this intake's lifecycle."""
    from threading import Event
    from .provider_priority import CurrentProviderPriorityIntake
    clock = clock or SystemClock()
    stopped = Event()
    intake = CurrentProviderPriorityIntake(connections=connections, clock=clock,
        operation_stopped=stopped.is_set)
    runtime, schedule = create_current_provider_worker(workspace=workspace, connections=connections,
        priority_intake=intake, clock=clock, forward_window=forward_window,
        transport_factory=transport_factory)
    stop = runtime.request_stop
    def request_stop():
        stopped.set()
        stop()
    runtime.request_stop = request_stop
    return runtime, schedule


def main():
    import argparse
    import os
    parser = argparse.ArgumentParser(description="Run the separately opted-in Games feedback worker")
    parser.add_argument("--workspace", default="legacy")
    parser.add_argument("--preview",action="store_true",help="Read saved scopes and next forward windows; no imports/writes")
    parser.add_argument("--follow",action="store_true",help="Explicitly advance consecutive configured windows")
    parser.add_argument("--overlap-seconds",type=int,help="Explicit overlap for --follow (0..3600)")
    parser.add_argument("--once", action="store_true", help="Drain ready work, then exit; retains durable retry/checkpoint state")
    parser.add_argument("--max-units", type=int, default=50, help="Maximum bounded units in one --once invocation (1..10000)")
    options = parser.parse_args()
    if not 1 <= options.max_units <= 10000:
        parser.error("--max-units must be 1..10000")
    if options.follow and options.overlap_seconds is None:
        parser.error("--follow requires explicit --overlap-seconds")
    policy=ForwardWindow(options.overlap_seconds) if options.follow else None
    if options.preview:
        import json
        dsn=os.environ.get("TEMPO_DATABASE_READ_URL")
        if not dsn:raise RuntimeError("TEMPO_DATABASE_READ_URL is required for read-only preview")
        store=PostgresReviewStore(DedicatedGamesConnections(dsn,read_only=True))
        result=[]
        for provider in ("lichess","chesscom"):
            configuration=store.configuration(options.workspace,provider)
            if configuration is None:
                result.append(dict(provider=provider,configured=False));continue
            previous=store.last_job(options.workspace,provider)
            job=(policy.next_job(configuration,previous,now=SystemClock().now(),build_job=configured_job)
                if policy is not None else None)
            result.append(dict(provider=provider,configured=True,
                configuration=configuration.model_dump(mode="json"),
                previous=None if previous is None else previous.model_dump(mode="json"),
                next_window=None if job is None else job.window.model_dump(mode="json"),
                executable=False,reason="preview_only_worker_opt_in_required"))
        print(json.dumps(dict(read_only=True,providers=result)));return
    if os.environ.get("TEMPO_PRODUCT_GAME_SYNC_ENABLED")!="1":
        raise RuntimeError("TEMPO_PRODUCT_GAME_SYNC_ENABLED=1 is required; no worker starts automatically")
    dsn = os.environ.get("TEMPO_DATABASE_WRITE_URL")
    if not dsn:
        raise RuntimeError("TEMPO_DATABASE_WRITE_URL is required")
    runtime, review = current_provider_worker(workspace=options.workspace,
        connections=DedicatedGamesConnections(dsn),forward_window=policy)
    first_schedule=True
    def enqueue_selected():
        nonlocal first_schedule
        if options.once:
            if not options.follow or not first_schedule:
                return False
            first_schedule=False
        return review.enqueue_due()
    units = run_optional_worker(runtime, enqueue_due=enqueue_selected,
        stop_when_idle=options.once, max_units=options.max_units if options.once else None)
    if options.once:
        import json
        print(json.dumps({"bounded_units": units, "durable_checkpoints_retained": True}))


if __name__ == "__main__":
    main()
