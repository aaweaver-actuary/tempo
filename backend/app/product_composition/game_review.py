"""Private concrete Games HTTP facade and the existing Study receiver bridge."""
from datetime import datetime
from datetime import timezone
from threading import Lock
import os
import time
import json

from app.games.feedback.review import GameReview, ReviewUnavailable, SyncConfiguration
from app.study.game_feedback import GameFeedback
from app.repertoire import project_capture
from tempo_legacy_database.adapters.games.product_review import DedicatedGamesConnections, PostgresReviewStore, fence_imported_game
from tempo_legacy_database.adapters.games.product_feedback import PostgresGamesStore
from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
from tempo_legacy_database.adapters.study.game_feedback_authority import current_owner_game_feedback


def configuration_policy(configuration):
    from .game_sync import AcquisitionPolicy
    return AcquisitionPolicy(provider=configuration.provider, speeds=configuration.speeds,
                             rated=configuration.rated, source_revision=1)


def configured_job(configuration, operation_id):
    from .game_sync import sync_job
    return sync_job(job_id=operation_id, workspace_id=configuration.workspace_id,
        account=configuration.account, window=configuration.window,
        repertoire_ids=configuration.repertoire_ids, policy=configuration_policy(configuration)).model_copy(
            update={"configuration_revision": configuration.revision})


def create_game_review(*, workspace, connections, learning, clock, runtime=None):
    class UnavailableSnapshot:
        def snapshot(self, repertoire):
            raise ReviewUnavailable("imports_belong_to_the_optional_worker")
    if getattr(connections, "read_only", False):
        def delivery(_):
            raise ReviewUnavailable("games_writer_profile_required", 503)
    elif runtime is None and (not callable(getattr(learning,"prepare",None))
            or not callable(getattr(learning,"accept_qualified",None))):
        def delivery(_):
            raise ReviewUnavailable("qualified_provider_encounter_receiver_required",503)
    else:
        from app.games.feedback.coordinator import Coordinator
        from .game_sync import WorkflowComparer, WorkflowNormalizer
        delivery = runtime.runner.coordinator.deliver if runtime is not None else Coordinator(
            providers={}, normalizer=WorkflowNormalizer(), comparer=WorkflowComparer(),
            snapshots=UnavailableSnapshot(), store=PostgresGamesStore(connections),
            learning=learning, clock=clock,qualifier=learning).deliver
    return GameReview(workspace=workspace, store=PostgresReviewStore(connections),
        learning=learning, build_job=configured_job, deliver=delivery, now=clock.now)


class _NoAdmissionMapping:
    def current(self, lapse):
        return ()


class CurrentStudyFeedback:
    """Use #487's public factory with Games authentication supplied by its owner.

    capture_for(lapse) runs before a writer transaction. fence_game(database,
    lapse,capture) authenticates/fences the canonical imported game in that
    borrowed transaction. It is a Games capability, never a Study callback.
    The receiver alone validates repertoire/card facts and mutates Study.
    """
    def __init__(self, *, transactions, capture_for, fence_game, clock):
        self.transactions, self.capture_for = transactions, capture_for
        self.fence_game, self.clock = fence_game, clock

    def _now(self):
        return datetime.fromisoformat(self.clock.utc_now().replace("Z", "+00:00"))

    def accept_lapse(self, lapse):
        retained = self.capture_for(lapse)
        snapshot = project_capture(retained)
        with self.transactions() as database:
            self.fence_game(database, lapse, retained)
            owner = current_owner_game_feedback(database, retained_owner_capture=retained,
                                                snapshot=snapshot, clock=self._now)
            receipt = owner.accept_lapse(lapse)
        return receipt  # Acknowledge only after the caller commits.

    def control_effect(self, command):
        with self.transactions() as database:
            owner = GameFeedback(PostgresGameFeedbackStore(database), _NoAdmissionMapping(), clock=self._now)
            receipt = owner.control_effect(command)
        return receipt

    def effect_status(self, key):
        with self.transactions() as database:
            owner = GameFeedback(PostgresGameFeedbackStore(database), _NoAdmissionMapping(), clock=self._now)
            return owner.effect_status(key)


class CurrentStudyReceiptReads:
    """Public status capability backed only by a bounded reader transaction.

    No source capture, admission, queue refresh or write capability is needed.
    The receiving owner reconstructs its own receipt from its retained effect.
    """
    def __init__(self, connections, clock):
        self.connections, self.clock = connections, clock

    def _status(self, method, key):
        # Resolve cold dependencies before opening the existing 250ms section.
        from app.study.encounter_feedback import EncounterFeedback
        from tempo_legacy_database.adapters.postgres_store import PostgresConnection
        with self.connections() as raw:
            owner = EncounterFeedback(
                PostgresGameFeedbackStore(PostgresConnection(raw, read_only=True)), None,
                clock=lambda: datetime.fromisoformat(self.clock.utc_now().replace("Z", "+00:00")),
                introductions=None, introduction_policy=None)
            return getattr(owner, method)(key)

    def encounter_status(self, key):
        return self._status("encounter_status", key)

    def effect_status(self, key):
        return self._status("effect_status", key)


class SystemClock:
    def now(self):
        return time.time()

    def utc_now(self):
        return datetime.now(timezone.utc).isoformat()


class CurrentGameReviewOperations:
    """Lazy root registration: construction opens no DB, capture or worker.

    Source capture reuses the existing backend owner's bounded read projection;
    no source facts/validation are reconstructed by this composition wrapper.
    """
    def __init__(self, *, workspace="legacy"):
        self.workspace, self._services, self._lock = workspace, {}, Lock()

    def _operations(self, *, writing=False):
        with self._lock:
            if writing not in self._services:
                dsn = os.environ.get("TEMPO_DATABASE_WRITE_URL" if writing else "TEMPO_DATABASE_READ_URL")
                if not dsn:
                    raise ReviewUnavailable("games_writer_profile_required" if writing else "games_reader_profile_required", 503)
                connections = DedicatedGamesConnections(dsn, read_only=not writing)
                clock = SystemClock()
                if writing:
                    def capture_for(lapse):
                        from app.legacy_game_observation import capture_game
                        return capture_game(lapse.finding.repertoire_id, lapse.key.game_id,
                                            (lapse.key.occurrence_ply - 1,))
                    learning = CurrentStudyFeedback(transactions=connections.writer,
                        capture_for=capture_for, fence_game=fence_imported_game, clock=clock)
                else:
                    learning = CurrentStudyReceiptReads(connections, clock)
                self._services[writing] = create_game_review(workspace=self.workspace, connections=connections,
                                                             learning=learning, clock=clock)
            return self._services[writing]

    def _read(self, method, *args, **request):
        try:
            return getattr(self._operations(), method)(*args, **request)
        except ReviewUnavailable:
            raise
        except Exception as error:
            # Optional storage/receipt failures stay within this request; no
            # stale cached admission and no Study startup/readiness dependency.
            raise ReviewUnavailable("game_review_read_unavailable", 503) from error

    def recent_games(self):
        return self._read("recent_games")

    def game_detail(self, **request):
        return self._read("game_detail", **request)

    def sync(self, **request):
        from app.command_dispatch import dispatch_command
        return dispatch_command("product_games.sync", request,
            idempotency_key=request["operation_id"])

    def retry_feedback(self, **request):
        from app.command_dispatch import dispatch_command
        return dispatch_command("product_games.retry_feedback", {
            **request, "finding": request["finding"].model_dump(mode="json")},
            idempotency_key=request["operation_id"])

    def configuration(self, provider):
        return self._read("configuration", provider)

    def configure(self, configuration, expected_revision, *, operation_id=None):
        from app.command_dispatch import dispatch_command
        result = dispatch_command("product_games.configure", {
            "configuration": configuration.model_dump(mode="json"), "expected_revision": expected_revision},
            idempotency_key=operation_id)
        # The command receipt is JSON-native. Reparse through the immutable
        # wire model before FastAPI's strict Python response validation; an
        # actual pending JSONResponse must retain its operation envelope.
        return SyncConfiguration.model_validate_json(json.dumps(result)) if isinstance(result, dict) else result


def current_game_review_router():
    from app.games.feedback.http import create_game_review_router
    return create_game_review_router(CurrentGameReviewOperations())
