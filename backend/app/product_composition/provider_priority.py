"""Optional-worker composition of the public, durable Study priority intake.

Capture closes before the borrowed writer opens. Receiving acceptance records
the owner's obligation only; independent Study refresh owns queue availability.
"""
from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime
from functools import partial
import logging
import re

from app.contracts.product_v02.learning import FirstDepartureWitness
from app.games.feedback.ports import DeliveryDeferred
from app.games.feedback.qualification import held_delivery, qualify_delivery
from app.repertoire import project_capture, project_published_repertoire
from .encounters import receive_admitted_encounter
from .game_review import CurrentStudyFeedback, CurrentStudyReceiptReads
from .retained_game import retained_canonical_game

_LOGGER = logging.getLogger("tempo.games.priority_intake")
_CAPTURE_REASONS = frozenset({
    "legacy_game_already_accepted_use_retained_preview",
    "one_explicit_existing_game_required",
    "explicit_decision_scope_exceeds_bound_or_is_invalid",
    "legacy_game_changed_or_accepted_during_export",
    "capture_time_requires_timezone",
    "legacy_completed_game_required",
    "legacy_game_time_unknown_or_changed",
    "canonical_imported_source_changed",
    "retained_owner_capture_required",
    "retained_owner_capture_changed",
    "retained_owner_projection_mismatch",
    "retained_owner_scope_mismatch",
    "retained_owner_scope_exceeds_bound",
    "borrowed_owner_writer_transaction_required",
    "qualified_configuration_busy", "qualified_configuration_changed",
    "qualified_configuration_scope_mismatch", "qualified_selection_scope_changed",
    "qualified_source_unavailable", "qualified_source_changed",
    "qualified_source_identity_changed", "qualified_original_effect_unproved",
    "qualified_effect_controlled", "qualified_canonical_source_changed",
    "qualification_original_command_mismatch",
})


def _failure_code(error):
    # Exception messages/tracebacks may contain SQL parameters or private PGN.
    # Only these existing fixed capture reasons and a five-character SQLSTATE
    # are safe to include. The original error still propagates unchanged.
    reason = getattr(error, "reason", None)
    if not isinstance(reason, str) or reason not in _CAPTURE_REASONS:
        reason = str(error) if type(error) is ValueError and str(error) in _CAPTURE_REASONS else None
    sqlstate = getattr(error, "sqlstate", None)
    return reason or (sqlstate if isinstance(sqlstate, str) and re.fullmatch(r"[0-9A-Z]{5}", sqlstate) else "unclassified")


@dataclass(frozen=True)
class PreparedProviderFeedback:
    command: object
    target_capture: dict
    target_snapshot: object
    qualification: FirstDepartureWitness
    prepared_decisions: object


@dataclass(frozen=True)
class LocalFeedbackProgress:
    progressed: bool
    pending: bool
    held_reason: str | None = None


def _owner_capture(connections, supplied=None):
    """Keep legacy capture's finite proofs while using the supplied read owner.

    Its historical defaults use background admission. An explicit next-card
    command supplies its own bounded read capability and must not wait for a
    quiet foreground merely because the default exporter did so.
    """
    from app.legacy_game_observation import capture_game
    if supplied is not None and supplied is not capture_game:
        return supplied
    from tempo_legacy_database.adapters.games.authority import headers
    from tempo_legacy_database.adapters.postgres_store import PostgresConnection
    from app.services.postgres_integrity import current_integrity_certificate

    def rows(statement, parameters=()):
        with connections() as database:
            return [dict(row) for row in database.execute(statement, parameters).fetchall()]

    def metadata(repertoire_id):
        facts = headers.repertoire_metadata(rows, repertoire_id)
        if len(facts) != 1:
            raise DeliveryDeferred("current_repertoire_authority_unavailable")
        with connections() as raw:
            certificate = current_integrity_certificate(PostgresConnection(raw), repertoire_id,
                facts[0]["graph_generation"])
        return {**facts[0], "integrity_current": certificate is not None,
                "integrity_certificate": certificate}

    def capture(repertoire_id, game_id, plies):
        return capture_game(repertoire_id, game_id, plies, reader=rows, metadata=metadata)
    return capture


def _qualified_facts(command, *, connections, clock, delivery=None, selector=None):
    """Read bounded source/config/publications; close connections before replay."""
    from tempo_legacy_database.adapters.authored_repertoires.product_snapshot import read_repertoire
    from tempo_legacy_database.adapters.games.product_review import PostgresReviewStore
    from app.games.feedback.coordinator import delivery_for
    if selector is None:
        from app.games.feedback.interfaces import select_first_boundary
        selector = select_first_boundary
    reads = PostgresReviewStore(connections)
    configuration = reads.configuration(command.key.workspace_id, command.key.provider)
    source = reads.source_detail(command.key.workspace_id, command.key.provider, command.key.game_id)
    if delivery is None:
        if source is None:
            raise DeliveryDeferred("first_departure_source_unavailable")
        original = delivery_for(source.source_key, command)
        delivery = reads.delivery(original.operation_id) or original
    publications, snapshots, unavailable = [], [], []
    if configuration is not None:
        for repertoire_id in configuration.repertoire_ids:
            try:
                with connections() as database:
                    facts = read_repertoire(database, repertoire_id)
                # Chess work runs after that read transaction/connection closes.
                snapshot = project_published_repertoire(facts)
            except ValueError:
                unavailable.append(repertoire_id)
            else:
                publications.append(facts)
                snapshots.append(snapshot)
    original_determination = delivery.determined_at or getattr(source, "determined_at", None) or clock.utc_now()
    qualified = qualify_delivery(delivery, source=source, configuration=configuration,
        snapshots=tuple(snapshots), unavailable=tuple(unavailable),
        determined_at=original_determination, retry_at=clock.now() + 30,
        select_first_boundary=selector)
    return qualified, source, configuration, tuple(publications), selector


def _prepare_qualified_owner(command, qualification, *, source, configuration,
                             publications, selector, capture):
    """Prepare global publication proof and exact target proof separately."""
    from tempo_legacy_database.adapters.games.product_feedback import fence_qualified_feedback
    from tempo_legacy_database.adapters.study.game_feedback_authority import CurrentOwnerDecisions
    decision = qualification.selection.decision
    target_capture = capture(decision.repertoire_id, command.key.game_id,
        (command.key.occurrence_ply - 1,))
    game = retained_canonical_game(target_capture)
    target_snapshot = project_capture(target_capture, comparison_game=game)
    decisions = CurrentOwnerDecisions(None, retained_owner_capture=target_capture,
        snapshot=target_snapshot).prepare_qualified(command, qualification,
            qualified_publications=publications,
            qualification_fence=partial(fence_qualified_feedback,
                expected_configuration=configuration, expected_source=source),
            game=game, select_first_boundary=selector)
    return PreparedProviderFeedback(command, target_capture, target_snapshot, qualification, decisions)


def prepare_feedback_qualification(command, *, connections=None, read=None, clock,
                                   qualification=None, determination=None, stream=None,
                                   capture=None, selector=None):
    """Public local preparation for pending or already acknowledged obligations.

    This entry owns no writer/dispatch and never waits for optional process
    health. Study maintenance may use it for an outstanding old priority receipt
    even when Games no longer has a pending delivery to retry.
    """
    from app.games.feedback.coordinator import delivery_for
    from tempo_legacy_database.adapters.games.product_review import PostgresReviewStore
    if connections is None:
        if not callable(read):
            raise DeliveryDeferred("first_departure_owner_read_required")
        @contextmanager
        def connections():
            with read() as database:
                yield database.raw
    if not callable(getattr(clock, "utc_now", None)):
        original_clock = clock
        class OwnerClock:
            def now(self):
                return original_clock().timestamp()
            def utc_now(self):
                value = original_clock()
                if value.tzinfo is None:
                    raise DeliveryDeferred("first_departure_aware_clock_required")
                return value.isoformat()
        clock = OwnerClock()
    capture = _owner_capture(connections, capture)
    source = PostgresReviewStore(connections).source_detail(
        command.key.workspace_id, command.key.provider, command.key.game_id)
    if source is None:
        raise DeliveryDeferred("first_departure_source_unavailable")
    delivery = delivery_for(source.source_key, command)
    existing = PostgresReviewStore(connections).delivery(delivery.operation_id)
    delivery = existing or delivery
    if qualification is not None:
        if not qualification.matches(command):
            raise DeliveryDeferred("qualification_original_command_mismatch")
        determination, stream = qualification.determined_at, qualification.stream
    if determination is not None:
        if delivery.determined_at is not None and delivery.determined_at != determination:
            raise DeliveryDeferred("first_departure_determination_conflict")
        delivery = delivery.model_copy(update={"determined_at": determination})
    if stream is not None:
        if delivery.stream is not None and delivery.stream != stream:
            raise DeliveryDeferred("first_departure_stream_conflict")
        delivery = delivery.model_copy(update={"stream": stream})
    qualified, source, configuration, publications, selector = _qualified_facts(command,
        connections=connections, clock=clock, delivery=delivery, selector=selector)
    if qualified.qualification_status != "ready":
        raise DeliveryDeferred(qualified.qualification_reason or "first_departure_unavailable")
    return _prepare_qualified_owner(command, qualified.qualification, source=source,
        configuration=configuration, publications=publications, selector=selector, capture=capture)


def _admitted(admitted):
    try:
        return callable(admitted) and admitted() is True
    except Exception:
        return False  # Optional mode evidence cannot become a Study health gate.


def pending_determined_feedback(*, workspace, connections, admitted):
    """Read-only predicate for composition; absent mode evidence denies intake."""
    if not _admitted(admitted):
        return False
    from tempo_legacy_database.adapters.games.product_feedback import PostgresGamesStore
    try:
        return PostgresGamesStore(connections).has_determined_delivery(workspace)
    except Exception:
        return False


def advance_determined_feedback(*, workspace, connections, clock, admitted, limit=1):
    """One mode-admitted local unit before a head writer; no provider factory."""
    if limit != 1:
        raise ValueError("local_feedback_is_one_bounded_unit")
    if not _admitted(admitted):
        return LocalFeedbackProgress(False, False, "feedback_mode_not_admitted")
    from tempo_legacy_database.adapters.games.product_feedback import PostgresGamesStore
    from app.games.feedback.coordinator import Coordinator
    store = PostgresGamesStore(connections)
    try:
        delivery = store.pending_delivery(clock.now(), ready_only=True, workspace=workspace)
        if delivery is None:
            return LocalFeedbackProgress(False, False)
        intake = CurrentProviderPriorityIntake(connections=connections, clock=clock,
            operation_stopped=lambda: not _admitted(admitted), admitted=admitted)
        coordinator = Coordinator(providers={}, normalizer=None, comparer=None,
            snapshots=None, store=store, learning=intake, clock=clock, qualifier=intake)
        coordinator.deliver(delivery)
        actual = store.delivery(delivery.operation_id)
        return LocalFeedbackProgress(actual.state == "acknowledged",
            store.has_determined_delivery(workspace), actual.qualification_reason or actual.error)
    except Exception as error:
        return LocalFeedbackProgress(False, True, "local_feedback_" + _failure_code(error))


class CurrentProviderPriorityIntake:
    def __init__(self, *, connections, clock, operation_stopped, capture=None, admitted=None,
                 selector=None):
        if not callable(operation_stopped):
            raise ValueError("explicit_provider_operation_control_required")
        self.connections, self.clock = connections, clock
        self.operation_stopped, self.capture = operation_stopped, _owner_capture(connections, capture)
        self.admitted, self.selector = admitted, selector
        self._qualification_facts = None  # One unit only, never a TTL/global cache.
        self.reads = CurrentStudyReceiptReads(connections, clock)
        # Controls share the existing owner's ledger; they never promote a queue.
        self.controls = CurrentStudyFeedback(transactions=connections.writer,
            capture_for=None, fence_game=None, clock=clock)

    def prepare(self, delivery):
        """The coordinator persists this qualification before any Study call."""
        self._qualification_facts = None
        try:
            facts = _qualified_facts(delivery.command, connections=self.connections,
                clock=self.clock, delivery=delivery, selector=self.selector)
        except Exception:
            return held_delivery(delivery, "first_departure_preparation_unavailable",
                retry_at=self.clock.now() + 30)
        qualified = facts[0]
        if qualified.qualification_status == "ready":
            self._qualification_facts = facts
        return qualified

    def prepare_qualified(self, command, *, qualification):
        """All source capture/chess preparation finishes before opening a writer."""
        facts = self._qualification_facts
        self._qualification_facts = None
        if facts is None or facts[0].command != command or facts[0].qualification != qualification:
            return prepare_feedback_qualification(command, connections=self.connections,
                clock=self.clock, qualification=qualification, capture=self.capture,
                selector=self.selector)
        _, source, configuration, publications, selector = facts
        return _prepare_qualified_owner(command, qualification, source=source,
            configuration=configuration, publications=publications, selector=selector, capture=self.capture)

    def _require_local_admission(self):
        if self.admitted is not None:
            if not _admitted(self.admitted):
                raise DeliveryDeferred("feedback_mode_not_admitted")
        else:
            from .game_sync_current import _require_current_sync_controls
            from app.services.redis_admission_gate import BackgroundAdmissionDeferred
            try:
                _require_current_sync_controls(allow_foreground=True)
            except BackgroundAdmissionDeferred as error:
                raise DeliveryDeferred("feedback_operation_not_admitted") from error
        stopped = self.operation_stopped()
        if type(stopped) is not bool or stopped:
            raise DeliveryDeferred("feedback_operation_stopped_or_unknown")

    def accept_qualified(self, command, *, qualification):
        """Accept the original command at the same Study owner with real fences."""
        from tempo_legacy_database.adapters.study.game_feedback_authority import CurrentDecisionUnavailable
        from app.games.feedback.review import ReviewUnavailable
        self._require_local_admission()
        try:
            prepared = self.prepare_qualified(command, qualification=qualification)
            return self.accept_prepared(prepared)
        except DeliveryDeferred:
            raise
        except (CurrentDecisionUnavailable, ReviewUnavailable, ValueError) as error:
            code = _failure_code(error)
            if code == "unclassified":
                code = "first_departure_current_authority_unavailable"
            raise DeliveryDeferred(code) from error

    def accept_prepared(self, prepared):
        from tempo_legacy_database.adapters.study.game_feedback_authority import current_owner_encounter_intake
        self._require_local_admission()
        with self.connections.writer() as database:
            receiver = current_owner_encounter_intake(database,
                retained_owner_capture=prepared.target_capture, snapshot=prepared.target_snapshot,
                prepared_decisions=prepared.prepared_decisions, qualification=prepared.qualification,
                clock=lambda: datetime.fromisoformat(self.clock.utc_now().replace("Z", "+00:00")))
            receipt = receiver.accept_qualified(prepared.command, qualification=prepared.qualification)
        return receipt  # Commit is complete; loss of this acknowledgment is safe.

    def accept_encounter(self, command):
        from .game_sync_current import _require_current_sync_controls
        stage = "controls"
        try:
            _require_current_sync_controls()
            stage = "capture"
            captured = self.capture(command.decision.repertoire_id, command.key.game_id,
                                    (command.key.occurrence_ply - 1,))
            stage = "projection"
            snapshot = project_capture(captured, comparison_game=retained_canonical_game(captured))
            stage = "writer"
            with self.connections.writer() as database:
                stage = "intake"
                receipt = receive_admitted_encounter(database, capture=captured, snapshot=snapshot,
                    command=command, operation_stopped=self.operation_stopped,
                    clock=lambda: datetime.fromisoformat(self.clock.utc_now().replace("Z", "+00:00")))
                stage = "commit"
        except Exception as error:
            _LOGGER.warning("Games priority intake deferred stage=%s error=%s code=%s",
                            stage, type(error).__name__, _failure_code(error))
            raise
        return receipt  # The delivery acknowledgment follows this commit.

    def control_effect(self, command):
        return self.controls.control_effect(command)

    def encounter_status(self, key):
        return self.reads.encounter_status(key)

    def effect_status(self, key):
        return self.reads.effect_status(key)

    def accept_lapse(self, command):
        raise ValueError("encounter_worker_does_not_create_legacy_lapses")
