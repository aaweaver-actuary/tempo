"""Lazy local operator composition for the schema051 retained-history path.

No provider sync, timer, dispatcher registration or database access starts here.
The supplied whole-operation admission must deny unknown control evidence and
honour foreground/maintenance safety. Its installation must exclude the legacy
unfiltered GameSyncCoordinator: the PostgreSQL Celery poller has an allowlist,
but app.background_worker starts that older coordinator independently.

Current retained controls (study_priority.control_snapshot) expose availability,
foreground activity and Study-only, with no separate full-Study-pause field.
Neither that absence nor a zero new-card cap is an admission decision. Only a
qualified admitted operation may bind the owner factory's paused=False; if a
distinct full-pause source is present, its actual decision must be supplied.
The receiving capability is injected by the explicit current-root composition.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import re
from threading import RLock
from urllib.parse import quote

from app.games.feedback.history import (
    HistorySelection, MAX_MANIFEST_BYTES, preview_history,
)
from app.games.feedback.history_http import HistoryUnavailable, PREFIX
from .game_history import (
    KIND, current_retained_history_comparer, history_run_status, run_history_once,
    start_history_in_transaction,
)


def _safe_conflict(error: ValueError) -> HistoryUnavailable:
    reason = str(error)
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,119}", reason):
        reason = "retained_history_request_conflict"
    return HistoryUnavailable(reason, 409)


class RetainedHistoryOperations:
    """Bounded operator service with explicit receiving and admission owners.

    Prepared plans stay in a bounded private process cache. The browser cannot
    replace detection/source/mapping/encounter facts. A restart or eviction of an
    unstarted preview requires a new preview. Started runs remain in the existing
    durable task ledger and resume/status do not depend on this cache.
    """

    def __init__(self, *, workspace_id: str, reader, comparer, read, write,
                 admission, receiver=None, clock=None, maximum_previews=8, claim=None):
        if (not isinstance(workspace_id, str) or not 1 <= len(workspace_id) <= 200
                or type(maximum_previews) is not int or not 1 <= maximum_previews <= 8):
            raise ValueError("explicit_bounded_retained_history_workspace_required")
        if not callable(read) or not callable(write):
            raise ValueError("retained_history_connection_capabilities_required")
        self.workspace_id = workspace_id
        self.reader, self.comparer = reader, comparer
        self.read, self.write = read, write
        self.admission, self.receiver = admission, receiver
        self.claim = claim
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.maximum_previews = maximum_previews
        self._plans = OrderedDict()
        self._plans_lock = RLock()
        self._operation_admitted = ContextVar("retained_history_operation_admitted", default=False)
        self._claimed_task = ContextVar("retained_history_claimed_task", default=None)

    @contextmanager
    def _admitted(self):
        if not callable(self.admission):
            raise HistoryUnavailable("retained_history_admission_unavailable")
        from app.services.activity_gate import BackgroundAdmissionDeferred
        admitted_token = self._operation_admitted.set(False)
        claimed_token = self._claimed_task.set(None)
        try:
            with self.admission():
                self._operation_admitted.set(True)
                yield
        except BackgroundAdmissionDeferred:
            raise HistoryUnavailable("retained_history_admission_deferred") from None
        finally:
            self._claimed_task.reset(claimed_token)
            self._operation_admitted.reset(admitted_token)

    def _claim_ready(self):
        if self._operation_admitted.get() is not True:
            raise HistoryUnavailable("retained_history_admission_unavailable")
        if self.claim is None:
            from app.services.durable_tasks import claim_task
            task = claim_task(KIND)
        else:
            task = self.claim()
        if task is not None and task.get("kind") != KIND:
            raise ValueError("retained_history_unexpected_claim_kind")
        self._claimed_task.set(task)
        return task

    def operation_stopped(self):
        """Actual admitted claimed-unit decision for the receiving owner.

        The public kind-scoped claim enforces durable per-work pause. The
        application additionally locks the current task generation/lease in its
        borrowed receipt transaction before invoking the receiver. This is the
        same minimal retained safety source used by Study's encounter sweep.
        Outside that admitted claimed unit, control evidence is unknown.
        """
        if self._operation_admitted.get() is not True or self._claimed_task.get() is None:
            return None
        return False

    def _selection(self, selection):
        if not isinstance(selection, HistorySelection) or selection.workspace_id != self.workspace_id:
            raise HistoryUnavailable("retained_history_workspace_mismatch", 409)
        return selection

    def _receiver(self):
        if self.receiver is None:
            raise HistoryUnavailable("retained_history_receiving_owner_unavailable")
        return self.receiver

    def preview(self, *, selection: HistorySelection, run_id: str) -> dict:
        selection = self._selection(selection)
        policy_identity = self.receiver.policy_identity if self.receiver is not None else None
        try:
            with self._admitted():
                moment = self.clock()
                if moment.tzinfo is None or moment.utcoffset() is None:
                    raise ValueError("retained_history_aware_clock_required")
                plan = preview_history(selection, reader=self.reader, comparer=self.comparer,
                    run_id=run_id, detected_at=moment.astimezone(timezone.utc).isoformat(),
                    policy_identity=policy_identity)
            # Bound private memory even for a preview which cannot execute.
            import json
            if len(json.dumps(plan.payload(), separators=(",", ":")).encode()) > MAX_MANIFEST_BYTES:
                raise ValueError("retained_history_manifest_exceeds_bound")
            with self._plans_lock:
                identity = (run_id, plan.digest)
                self._plans[identity] = plan
                self._plans.move_to_end(identity)
                while len(self._plans) > self.maximum_previews:
                    self._plans.popitem(last=False)
            return dict(run_id=run_id, manifest_digest=plan.digest,
                selection=selection.model_dump(mode="json"),
                preview=plan.preview.model_dump(mode="json"))
        except ValueError as error:
            raise _safe_conflict(error) from None

    def start(self, *, selection: HistorySelection, run_id: str, manifest_digest: str) -> dict:
        selection = self._selection(selection)
        receiver = self._receiver()
        with self._plans_lock:
            plan = self._plans.get((run_id, manifest_digest))
        if plan is None:
            raise HistoryUnavailable("retained_history_preview_required", 409)
        if plan.selection != selection:
            raise HistoryUnavailable("retained_history_selection_changed", 409)
        try:
            with self._admitted():
                with self.write() as database:
                    task = start_history_in_transaction(database, plan, receiver=receiver,
                                                        selection=selection)
            # The writer has committed. Queue acceptance is not Study admission.
            return dict(run_id=run_id, manifest_digest=plan.digest, state=task["state"],
                planned_encounters=len(plan.encounters),
                status_url=PREFIX + "/runs/" + quote(run_id, safe=""))
        except ValueError as error:
            raise _safe_conflict(error) from None

    def run_ready(self, *, maximum_units: int = 16) -> dict:
        receiver = self._receiver()
        try:
            results = run_history_once(receiver=receiver, write=self.write,
                admission=self._admitted, maximum_units=maximum_units, claim=self._claim_ready)
            return dict(scope="approved_retained_history_queue", maximum_units=maximum_units,
                        processed_units=len(results), slice_results=list(results))
        except ValueError as error:
            raise _safe_conflict(error) from None

    def status(self, *, run_id: str) -> dict | None:
        try:
            with self._admitted(), self.read() as database:
                return history_run_status(database, workspace_id=self.workspace_id, run_id=run_id)
        except ValueError as error:
            raise _safe_conflict(error) from None


def current_retained_history_operations(*, workspace_id: str, admission, receiver=None,
                                       receiver_factory=None):
    """Bind retained storage lazily; callers still supply actual qualified guards.

    No default receiver or admission exists. No full-D introduction store,
    provider acquisition, migration052 read model, or new daemon is imported.
    """
    from tempo_legacy_database.adapters.games.product_history import PostgresRetainedHistory
    from tempo_legacy_database.connections import background_read_connection, connection
    if receiver is not None and receiver_factory is not None:
        raise ValueError("one_retained_history_receiving_capability_required")
    operations = RetainedHistoryOperations(workspace_id=workspace_id,
        reader=PostgresRetainedHistory(background_read_connection),
        comparer=current_retained_history_comparer(), read=background_read_connection,
        write=lambda: connection(background=True), admission=admission, receiver=receiver)
    if receiver_factory is not None:
        operations.receiver = receiver_factory(operation_stopped=operations.operation_stopped,
                                               clock=operations.clock)
    return operations
