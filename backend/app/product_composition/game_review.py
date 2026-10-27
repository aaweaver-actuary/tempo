"""Private concrete Games HTTP facade and the existing Study receiver bridge."""
from datetime import datetime
from datetime import timezone
from threading import Lock
import os
import time

from app.games.feedback.interfaces import GameReview, ReviewUnavailable
from app.games.feedback.interfaces import Coordinator
from app.study.game_feedback import GameFeedback
from app.repertoire.interfaces import project_capture
from tempo_legacy_database.adapters.games.product_review import DedicatedGamesConnections, PostgresReviewStore, fence_imported_game
from tempo_legacy_database.adapters.games.product_feedback import PostgresGamesStore
from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
from tempo_legacy_database.adapters.study.game_feedback_authority import current_owner_game_feedback

from .game_sync import AcquisitionPolicy, WorkflowComparer, WorkflowNormalizer, sync_job


def configuration_policy(configuration):
    return AcquisitionPolicy(provider=configuration.provider, speeds=configuration.speeds,
                             rated=configuration.rated, source_revision=1)


def configured_job(configuration, operation_id):
    return sync_job(job_id=operation_id, workspace_id=configuration.workspace_id,
        account=configuration.account, window=configuration.window,
        repertoire_ids=configuration.repertoire_ids, policy=configuration_policy(configuration))


def create_game_review(*, workspace, connections, learning, clock, runtime=None):
    class UnavailableSnapshot:
        def snapshot(self, repertoire):
            raise ReviewUnavailable("imports_belong_to_the_optional_worker")
    delivery = runtime.runner.coordinator.deliver if runtime is not None else Coordinator(
        providers={}, normalizer=WorkflowNormalizer(), comparer=WorkflowComparer(),
        snapshots=UnavailableSnapshot(), store=PostgresGamesStore(connections),
        learning=learning, clock=clock).deliver
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
        self.workspace, self._service, self._lock = workspace, None, Lock()

    def _operations(self):
        with self._lock:
            if self._service is None:
                dsn = os.environ.get("TEMPO_DATABASE_WRITE_URL")
                if not dsn:
                    raise ReviewUnavailable("games_storage_unavailable", 503)
                connections = DedicatedGamesConnections(dsn)
                clock = SystemClock()
                def capture_for(lapse):
                    from app.legacy_game_observation import capture_game
                    return capture_game(lapse.finding.repertoire_id, lapse.key.game_id,
                                        (lapse.key.occurrence_ply - 1,))
                learning = CurrentStudyFeedback(transactions=connections.writer,
                    capture_for=capture_for, fence_game=fence_imported_game, clock=clock)
                self._service = create_game_review(workspace=self.workspace, connections=connections,
                                                   learning=learning, clock=clock)
            return self._service

    def recent_games(self):
        return self._operations().recent_games()

    def game_detail(self, **request):
        return self._operations().game_detail(**request)

    def sync(self, **request):
        return self._operations().sync(**request)

    def retry_feedback(self, **request):
        return self._operations().retry_feedback(**request)

    def configuration(self, provider):
        return self._operations().configuration(provider)

    def configure(self, configuration, expected_revision):
        return self._operations().configure(configuration, expected_revision)


def current_game_review_router():
    from app.games.feedback.http import create_game_review_router
    return create_game_review_router(CurrentGameReviewOperations())
