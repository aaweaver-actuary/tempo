"""Opt-in local operator binding; construction never opens a database.

The release owner must qualify PostgreSQL/Celery and absence of the old
unfiltered background worker before setting the two explicit activation values.
No live activation is performed by this source module.
"""
from contextlib import contextmanager
import os
from threading import RLock, Semaphore

from app.games.feedback.history_http import HistoryUnavailable, create_game_history_router

POLICY_IDENTITY = "real_game_encounter_exception_v1"
_operator_slot = Semaphore(1)


def _configured():
    from tempo_legacy_database.adapters import postgres_store
    if (os.getenv("TEMPO_RETAINED_HISTORY_ENABLED") != "1"
            or os.getenv("TEMPO_RETAINED_HISTORY_QUEUE_MODE") != "postgres-celery"
            or not postgres_store.configured()):
        raise HistoryUnavailable("retained_history_not_enabled")


def _controls_available():
    from app.services.study_priority import control_snapshot
    from app.services.redis_admission_gate import BackgroundAdmissionDeferred
    policy = control_snapshot()
    if (policy.get("available") is not True or type(policy.get("active")) is not bool
            or type(policy.get("study_only_mode")) is not bool):
        raise BackgroundAdmissionDeferred("Retained history control is unavailable")
    if policy["active"]:
        raise BackgroundAdmissionDeferred("Waiting for foreground activity")
    # Study-only is readable control evidence, not a stop for this explicitly
    # approved encounter exception. No cap/settings mutation or full-pause guess.


@contextmanager
def current_history_admission():
    from app.services.activity_gate import activity_gate
    from app.services.redis_admission_gate import BackgroundAdmissionDeferred
    _configured()
    if not _operator_slot.acquire(blocking=False):
        raise BackgroundAdmissionDeferred("A retained history operation is active")
    try:
        with activity_gate.background_job("game_feedback_history", "operator", yielding=True):
            _controls_available()
            activity_gate.check_background_admission()
            yield
    finally:
        _operator_slot.release()


def _receiver(*, operation_stopped, clock):
    from app.study.encounter_feedback import EncounterIntroductionPolicy
    from .game_history import current_retained_history_receiver

    def stopped():
        _configured()
        _controls_available()
        return operation_stopped()

    return current_retained_history_receiver(policy_identity=POLICY_IDENTITY,
        operation_stopped=stopped,
        introduction_policy=EncounterIntroductionPolicy("encounter_exception"), clock=clock)


class CurrentHistoryOperations:
    """Lazy process-owned plan cache, with all work behind explicit admission."""
    def __init__(self):
        self._operations = None
        self._lock = RLock()

    def _current(self):
        _configured()
        with self._lock:
            if self._operations is None:
                from app.services.legacy_game_evidence_scope import LEGACY_WORKSPACE_ID
                from .game_history_runtime import current_retained_history_operations
                self._operations = current_retained_history_operations(
                    workspace_id=LEGACY_WORKSPACE_ID, admission=current_history_admission,
                    receiver_factory=_receiver if os.getenv("TEMPO_DATABASE_WRITE_URL") else None)
            return self._operations

    def _writer(self):
        _configured()
        if not os.getenv("TEMPO_DATABASE_WRITE_URL"):
            raise HistoryUnavailable("retained_history_writer_operator_required")
        return self._current()

    def preview(self, **arguments):
        return self._current().preview(**arguments)

    def start(self, **arguments):
        return self._writer().start(**arguments)

    def run_ready(self, **arguments):
        return self._writer().run_ready(**arguments)

    def status(self, **arguments):
        return self._current().status(**arguments)


def current_game_history_router():
    return create_game_history_router(CurrentHistoryOperations())
