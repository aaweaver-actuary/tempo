"""Explicit optional-process assembly over current source owners.

Importing this module starts nothing. The HTTP app does not import or invoke its
launcher; saved recurrence alone cannot activate a worker.
"""
from app.repertoire import project_published_repertoire
from app.games.feedback.review import ReviewUnavailable
from app.contracts.product_v02.learning import EncounterFeedbackPort
from tempo_legacy_database.adapters.authored_repertoires.product_snapshot import read_repertoire
from tempo_legacy_database.adapters.games.product_review import (
    DedicatedGamesConnections, PostgresReviewStore,
)
from tempo_legacy_database.adapters.games.product_sources import PostgresCanonicalGamesStore

from .game_review import SystemClock, configuration_policy, configured_job, create_game_review
from app.games.feedback.continuous import ContinuousSync, ForwardWindow
from .game_sync import create_game_sync
from .game_worker import run_optional_worker

from contextlib import contextmanager


def _require_current_sync_controls(*, allow_foreground=False):
    import os
    from app.services.redis_admission_gate import BackgroundAdmissionDeferred
    from app.services.study_priority import control_snapshot
    policy=control_snapshot()
    if (os.environ.get("TEMPO_PRODUCT_GAME_SYNC_ENABLED")!="1"
            or policy.get("available") is not True or type(policy.get("active")) is not bool
            or type(policy.get("study_only_mode")) is not bool
            or (policy["active"] and not allow_foreground)):
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
            facts = read_repertoire(database, repertoire_id)
        return project_published_repertoire(facts)


def create_current_game_sync(*, workspace, connections, clock=None,
                             learning=None, forward_window=None, transport_factory=None,
                             delivery_enabled: bool = True,
                             reconciliation_interval_seconds: int | None = None):
    """Build one opted-in worker using both explicitly saved provider policies.

    Comparison uses published source facts. Canonical Games publication and its
    original outbox commit before any enabled delivery. Collection needs no
    receiver and leaves commands pending. Delivery never waits for queue refresh.
    """
    if delivery_enabled and (learning is None
            or not callable(getattr(learning, "accept_qualified", None))
            or not callable(getattr(learning, "prepare", None))):
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
    if forward_window is not None:
        # Apply the existing pure follow policy before building provider clients
        # or claiming work. This validates recurring seed width/overlap without
        # enqueueing, changing a historical job or inventing a smaller window.
        for configuration in configurations:
            forward_window.next_job(configuration, None, now=clock.now(), build_job=configured_job)
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
    @contextmanager
    def configured_local_admission():
        # This grants only an already determined local delivery unit. Provider
        # acquisition/full capture keeps its stricter foreground admission.
        _require_current_sync_controls(allow_foreground=True)
        current = tuple(value for provider in ("lichess", "chesscom")
            if (value:=configuration_store.configuration(workspace,provider)) is not None)
        if current != configurations:
            raise ReviewUnavailable("sync_configuration_changed_restart_worker",503)
        yield
    store = PostgresCanonicalGamesStore(connections)
    runtime = create_game_sync(policies=tuple(configuration_policy(value) for value in configurations),
        store=store, snapshots=CurrentRepertoireSnapshots(connections), learning=learning,
        clock=clock, source_identity=store,
        transport_factory=transport_factory,encounter_feedback=True,admission=configured_admission,
        delivery_enabled=delivery_enabled, qualifier=learning if delivery_enabled else None,
        effect_identity=getattr(learning, "effect_identity", None) if delivery_enabled else None,
        local_admission=configured_local_admission if delivery_enabled else None)
    review = create_game_review(workspace=workspace, connections=connections,
                               learning=learning, clock=clock, runtime=runtime)
    reconciliation_options = ({} if reconciliation_interval_seconds is None else
        {"reconciliation_interval_seconds": reconciliation_interval_seconds})
    schedule=(ContinuousSync(workspace=workspace,store=configuration_store,policy=forward_window,
        build_job=configured_job,now=clock.now, **reconciliation_options)
        if forward_window is not None else review)
    return runtime, CurrentSyncSchedule(schedule)


def create_current_provider_worker(*, workspace, connections,
                                   priority_intake: EncounterFeedbackPort | None = None,
                                   clock=None, forward_window=None, transport_factory=None,
                                   delivery_enabled: bool = True,
                                   reconciliation_interval_seconds: int | None = None):
    """Wire the public intake only when delivery is enabled.

    Accepted/pending is evidence acceptance, not queue availability. The root
    supplies the receiver implementation; collection permits its absence. Games
    never constructs a Study owner, borrows its writer, or refreshes/promotes its
    queue during delivery.
    """
    if delivery_enabled and priority_intake is None:
        raise ReviewUnavailable("decoupled_priority_intake_required",503)
    options = {}
    if not delivery_enabled:
        options["delivery_enabled"] = False
    if reconciliation_interval_seconds is not None:
        options["reconciliation_interval_seconds"] = reconciliation_interval_seconds
    return create_current_game_sync(workspace=workspace, connections=connections,
        learning=priority_intake, clock=clock, forward_window=forward_window,
        transport_factory=transport_factory, **options)


def current_provider_worker(*, workspace, connections, clock=None, forward_window=None,
                            transport_factory=None, delivery_enabled: bool = True,
                            reconciliation_interval_seconds: int | None = None):
    """Concrete opt-in root: the worker alone owns this intake's lifecycle."""
    options = ({} if reconciliation_interval_seconds is None else
        {"reconciliation_interval_seconds": reconciliation_interval_seconds})
    if not delivery_enabled:
        return create_current_provider_worker(workspace=workspace, connections=connections,
            clock=clock, forward_window=forward_window, transport_factory=transport_factory,
            delivery_enabled=False, **options)
    from threading import Event
    from .provider_priority import CurrentProviderPriorityIntake
    clock = clock or SystemClock()
    stopped = Event()
    intake = CurrentProviderPriorityIntake(connections=connections, clock=clock,
        operation_stopped=stopped.is_set)
    runtime, schedule = create_current_provider_worker(workspace=workspace, connections=connections,
        priority_intake=intake, clock=clock, forward_window=forward_window,
        transport_factory=transport_factory, **options)
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
    parser.add_argument("--collect-only", action="store_true",
        help="Persist Games observations and pending commands without delivering to Study")
    parser.add_argument("--reconcile-interval-seconds", type=int,
        help="Explicit reconciliation interval for --follow (at least 60 seconds)")
    parser.add_argument("--overlap-seconds",type=int,help="Explicit overlap for --follow (0..3600)")
    parser.add_argument("--once", action="store_true", help="Drain ready work, then exit; retains durable retry/checkpoint state")
    parser.add_argument("--max-units", type=int, default=50, help="Maximum bounded units in one --once invocation (1..10000)")
    options = parser.parse_args()
    if not 1 <= options.max_units <= 10000:
        parser.error("--max-units must be 1..10000")
    if options.follow and options.overlap_seconds is None:
        parser.error("--follow requires explicit --overlap-seconds")
    if options.reconcile_interval_seconds is not None:
        if not options.follow:
            parser.error("--reconcile-interval-seconds requires --follow")
        if options.reconcile_interval_seconds < 60:
            parser.error("--reconcile-interval-seconds must be at least 60")
    policy=ForwardWindow(options.overlap_seconds) if options.follow else None
    if options.preview:
        import json
        dsn=os.environ.get("TEMPO_DATABASE_READ_URL")
        if not dsn:raise RuntimeError("TEMPO_DATABASE_READ_URL is required for read-only preview")
        store=PostgresReviewStore(DedicatedGamesConnections(dsn,read_only=True))
        now = SystemClock().now()
        streams = {state.configuration.provider: state
                   for state in store.continuous_state(options.workspace, now)}
        result=[]
        for provider in ("lichess","chesscom"):
            configuration=store.configuration(options.workspace,provider)
            if configuration is None:
                result.append(dict(provider=provider,configured=False));continue
            state = streams.get(provider)
            previous = None if state is None else state.forward_job
            job=(policy.next_job(configuration,previous,now=now,build_job=configured_job)
                if policy is not None and state is not None and state.forward_due else None)
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
    worker_options = {}
    if options.collect_only:
        worker_options["delivery_enabled"] = False
    if options.reconcile_interval_seconds is not None:
        worker_options["reconciliation_interval_seconds"] = options.reconcile_interval_seconds
    runtime, review = current_provider_worker(workspace=options.workspace,
        connections=DedicatedGamesConnections(dsn),forward_window=policy, **worker_options)
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
