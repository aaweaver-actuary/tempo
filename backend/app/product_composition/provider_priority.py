"""Optional-worker composition of the public, durable Study priority intake.

Capture closes before the borrowed writer opens. Receiving acceptance records
the owner's obligation only; independent Study refresh owns queue availability.
"""
from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime
from functools import partial
import logging
import json
import re

from app.contracts.product_v02.learning import FirstDepartureWitness
from app.games.feedback.ports import Delivery, DeliveryDeferred
from app.games.feedback.qualification import (acknowledge_control_delivery, acknowledge_qualified_delivery,
    delivery_for, held_delivery, qualify_delivery, select_first_boundary)
from app.repertoire import project_capture, PreparedPublishedRepertoire
from app.services.feedback_diagnostics import diagnose_feedback, feedback_span, feedback_work
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
    "managed_feedback_mode_not_active", "managed_feedback_runtime_identity_unavailable",
    "managed_feedback_runtime_identity_changed",
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
    require_active_mode: bool = False
    mode_runtime_id: str | None = None
    qualification_fence: object = None
    prepared_targets: tuple = ()


@dataclass(frozen=True)
class PreparedDeterminedFeedback:
    original_delivery: Delivery
    delivery: Delivery
    provider_feedback: PreparedProviderFeedback | None
    mode_runtime_id: str

    @property
    def priority(self):
        if self.is_control:
            return None
        return dict(urgent_stream=self.delivery.stream,
            urgent_determined_at=self.delivery.determined_at,
            urgent_effect_id=self.delivery.owner_effect_id)

    @property
    def is_control(self):
        return self.delivery.control is not None


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


def _current_feedback_targets(command, snapshots, *, connections, target_prepare=None):
    """Use only Study's explicit binding for the original logical decision.

    Complete publication/routes stay unchanged. Other decisions are never
    searched or guessed from a FEN, and a superseded mapping needs the same
    owner capability as any other current-presentation replacement.
    """
    if target_prepare is None:
        from tempo_legacy_database.adapters.study.recombination import prepare_game_feedback_target
        target_prepare = prepare_game_feedback_target
    from tempo_legacy_database.adapters.postgres_store import PostgresConnection
    @contextmanager
    def read_connection():
        with connections() as raw:
            yield PostgresConnection(raw)
    prepared, resolved, seen = [], [], {}
    mutable = {"presentation_id", "presentation_revision", "available", "introduction", "introduced_at",
        "introduction_matches_current_content", "introduced_before_game", "introduction_game"}
    for snapshot in snapshots:
        mappings = []
        for original in snapshot.decision_presentations:
            current = original
            if original.decision_id == command.key.decision_id:
                key = original.model_dump_json()
                if key not in seen:
                    try:
                        seen[key] = target_prepare(original, read_connection=read_connection)
                    except ValueError as error:
                        raise DeliveryDeferred("first_departure_target_lineage_unavailable",
                            retry_after=30) from error
                binding = seen[key]
                if binding is not None:
                    if (binding.original != original or any(getattr(binding.current,field) != getattr(original,field)
                            for field in type(original).model_fields if field not in mutable)):
                        raise DeliveryDeferred("first_departure_target_lineage_unproved",retryable=False)
                    current = binding.current
                    if binding not in prepared:
                        prepared.append(binding)
            mappings.append(current)
        resolved.append(snapshot.model_copy(update={"decision_presentations":tuple(mappings)}))
    return tuple(resolved), tuple(prepared)


def _qualified_facts(command, *, connections, clock, delivery=None, selector=None, target_prepare=None):
    """Read bounded source/config/publications; close connections before replay."""
    from tempo_legacy_database.adapters.authored_repertoires.product_snapshot import read_repertoire
    from tempo_legacy_database.adapters.games.product_review import PostgresReviewStore
    from inspect import signature
    if selector is None:
        selector = select_first_boundary
    reads = PostgresReviewStore(connections)
    with feedback_span('source_configuration_read'):
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
                # Production owners declare the snapshot keyword; legacy
                # injected no-argument readers retain their supplied contract.
                options = ({"repertoire_snapshot": True}
                    if "repertoire_snapshot" in signature(connections).parameters else {})
                with feedback_span('publication_read'):
                    with connections(**options) as database:
                        facts = read_repertoire(database, repertoire_id)
                # The adapter owns decoding; all chess projection follows exit.
                with feedback_span('publication_projection'):
                    publication = PreparedPublishedRepertoire(facts)
                    snapshot = publication.snapshot
            except ValueError:
                unavailable.append(repertoire_id)
            else:
                publications.append(publication)
                snapshots.append(snapshot)
    with feedback_span('current_target_prepare'):
        snapshots, prepared_targets = _current_feedback_targets(command, snapshots,
            connections=connections, target_prepare=target_prepare)
    original_determination = delivery.determined_at or getattr(source, "determined_at", None) or clock.utc_now()
    with feedback_span('first_boundary_qualification'):
        qualified = qualify_delivery(delivery, source=source, configuration=configuration,
            snapshots=tuple(snapshots), unavailable=tuple(unavailable),
            determined_at=original_determination, retry_at=clock.now() + 30,
            select_first_boundary=selector)
    from app.study.game_feedback import effect_id
    qualified = qualified.model_copy(update={"owner_effect_id": effect_id(command.key)})
    return qualified, source, configuration, tuple(publications), selector, prepared_targets


def _prepare_qualified_owner(command, qualification, *, source, configuration,
                             publications, selector, capture, require_active_mode=False, prepared_targets=()):
    """Prepare global publication proof and exact target proof separately."""
    from tempo_legacy_database.adapters.games.product_feedback import fence_qualified_feedback
    from tempo_legacy_database.adapters.study.game_feedback_authority import CurrentOwnerDecisions
    runtime_id = None
    if require_active_mode:
        from .managed_runtime import current_managed_runtime_identity
        runtime_id = current_managed_runtime_identity()
        if runtime_id is None:
            raise DeliveryDeferred("managed_feedback_runtime_identity_unavailable")
    decision = qualification.selection.decision
    with feedback_span('target_capture'):
        target_capture = capture(decision.repertoire_id, command.key.game_id,
            (command.key.occurrence_ply - 1,))
    with feedback_span('target_projection'):
        game = retained_canonical_game(target_capture)
        target_snapshot = project_capture(target_capture, comparison_game=game)
    source_fence = partial(fence_qualified_feedback,
        expected_configuration=configuration, expected_source=source,
        require_active_mode=require_active_mode, mode_runtime_id=runtime_id)
    with feedback_span('current_owner_validation'):
        decisions = CurrentOwnerDecisions(None, retained_owner_capture=target_capture,
            snapshot=target_snapshot).prepare_qualified(command, qualification,
                qualified_publications=publications,
                prepared_targets=prepared_targets,
                qualification_fence=source_fence,
                game=game, select_first_boundary=selector)
    return PreparedProviderFeedback(command, target_capture, target_snapshot, qualification, decisions,
        require_active_mode, runtime_id, source_fence, prepared_targets)


@diagnose_feedback
def prepare_feedback_qualification(command, *, connections=None, read=None, clock,
                                   qualification=None, determination=None, stream=None,
                                   capture=None, selector=None, require_active_mode=False):
    """Public local preparation for pending or already acknowledged obligations.

    This entry owns no writer/dispatch and never waits for optional process
    health. Study maintenance may use it for an outstanding old priority receipt
    even when Games no longer has a pending delivery to retry.
    """
    from tempo_legacy_database.adapters.games.product_review import PostgresReviewStore
    if connections is None:
        if not callable(read):
            raise DeliveryDeferred("first_departure_owner_read_required")
        @contextmanager
        def connections(*, repertoire_snapshot=False):
            from tempo_legacy_database.connections import read_connection
            options = ({"repertoire_snapshot": True} if repertoire_snapshot
                and read is read_connection else {})
            with read(**options) as database:
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
    qualified, source, configuration, publications, selector, prepared_targets = _qualified_facts(command,
        connections=connections, clock=clock, delivery=delivery, selector=selector)
    if qualified.qualification_status != "ready":
        reason = qualified.qualification_reason or "first_departure_unavailable"
        changed_authority = reason in {"first_departure_source_changed", "first_departure_configuration_mismatch",
            "first_departure_determination_conflict", "first_departure_stream_conflict"}
        raise DeliveryDeferred(reason, retry_after=1,
            retryable=(qualified.qualification_status != "ineligible"
                and qualified.qualification_retryable and not changed_authority))
    return _prepare_qualified_owner(command, qualified.qualification, source=source,
        configuration=configuration, publications=publications, selector=selector, capture=capture,
        require_active_mode=require_active_mode, prepared_targets=prepared_targets)


def _admitted(admitted):
    try:
        return callable(admitted) and admitted() is True
    except Exception:
        return False  # Optional mode evidence cannot become a Study health gate.


def pending_determined_feedback(*, workspace, connections, admitted, before_priority=None):
    """Read-only predicate for composition; absent mode evidence denies intake."""
    if not _admitted(admitted):
        return False
    from tempo_legacy_database.adapters.games.product_feedback import PostgresGamesStore
    try:
        bounds = _pending_priority_bounds(before_priority)
        if bounds is None:
            return False
        return PostgresGamesStore(connections).has_determined_delivery(workspace, **bounds)
    except Exception:
        return False


def _pending_priority_bounds(before_priority):
    if before_priority is None:
        return {}
    stream = before_priority.get("urgent_stream")
    determined_at = before_priority.get("urgent_determined_at")
    identity = before_priority.get("urgent_effect_id")
    if (stream not in {"forward", "reconciliation", "manual"}
            or not isinstance(determined_at, str)
            or not isinstance(identity, str) or not re.fullmatch(r"game-effect:[0-9a-f]{64}", identity)):
        return None
    parsed = datetime.fromisoformat(determined_at.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return None
    return dict(before_stream=stream, before_determined_at=determined_at, before_effect_id=identity)


def pending_determined_feedback_in_transaction(database, *, workspace, runtime_id, before_priority=None):
    """Optional bounded source hint in the caller's existing head transaction.

    The composition obtained the trusted process identity before this writer.
    No producer, filesystem, connection, preparation or acceptance runs here.
    Optional uncertainty returns false without poisoning ordinary Study.
    """
    from tempo_legacy_database.adapters.games.managed_mode import feedback_handoff_admitted_in_transaction
    from tempo_legacy_database.adapters.games.product_feedback import has_determined_delivery_in_transaction
    raw = getattr(database,"raw",database)
    if runtime_id is None or getattr(getattr(raw,"info",None),"transaction_status",None) is None:
        return False
    if raw.info.transaction_status.name != "INTRANS":
        return False
    try:
        bounds = _pending_priority_bounds(before_priority)
        if bounds is None:
            return False
        with raw.transaction():
            return (feedback_handoff_admitted_in_transaction(database,workspace,runtime_id=runtime_id)
                and has_determined_delivery_in_transaction(database,workspace,**bounds))
    except Exception:
        return False


def prepare_determined_feedback(*, workspace, connections, clock, admitted, capture=None,
                               selector=None, before_priority=None):
    """PURE preparation of one determined outbox unit, with every read closed.

    Selecting, qualification, target capture and chess projection cannot mutate
    either owner. A gateway may discard this object for a replayed/stale request.
    """
    if not _admitted(admitted):
        return None
    from .managed_runtime import current_managed_runtime_identity
    runtime_id = current_managed_runtime_identity()
    if runtime_id is None:
        return None
    from tempo_legacy_database.adapters.games.product_feedback import PostgresGamesStore
    from app.study.game_feedback import effect_id
    original = PostgresGamesStore(connections).pending_delivery(clock.now(),
        ready_only=True, workspace=workspace, priority_order=True)
    if original is None:
        return None
    if before_priority is not None and original.control is None:
        from app.services.queue_ordering import urgent_priority_key
        priority = dict(urgent_determined_at=original.determined_at,
            urgent_stream=original.stream, urgent_effect_id=effect_id(original.command.key))
        if urgent_priority_key(priority) >= urgent_priority_key(before_priority):
            return None
    return prepare_delivery_feedback(original,connections=connections,clock=clock,admitted=admitted,
        capture=capture,selector=selector)


@diagnose_feedback
def prepare_delivery_feedback(original, *, connections, clock, admitted, capture=None, selector=None):
    """Pure preparation of one server-read immutable delivery, including controls."""
    if not _admitted(admitted):
        return None
    feedback_work(original.operation_id, original.configuration_revision)
    from .managed_runtime import current_managed_runtime_identity
    from app.study.game_feedback import effect_id
    runtime_id = current_managed_runtime_identity()
    if runtime_id is None:
        return None
    if original.control is not None:
        return PreparedDeterminedFeedback(original,original,None,runtime_id)
    qualified, provider = original, None
    try:
        qualified, source, configuration, publications, selected, targets = _qualified_facts(
            original.command, connections=connections, clock=clock, delivery=original,
            selector=selector)
        if qualified.qualification_status == "ready":
            provider = _prepare_qualified_owner(original.command, qualified.qualification,
                source=source, configuration=configuration, publications=publications,
                selector=selected, capture=_owner_capture(connections, capture), require_active_mode=True,
                prepared_targets=targets)
    except DeliveryDeferred as error:
        qualified = held_delivery(qualified, error.reason,
            retry_at=clock.now() + error.retry_after, retryable=error.retryable)
    except Exception as error:
        qualified = held_delivery(qualified, "first_departure_preparation_unavailable",
            retry_at=clock.now() + 30)
        _LOGGER.warning("Local Games preparation deferred error=%s code=%s",
                        type(error).__name__, _failure_code(error))
    qualified = qualified.model_copy(update={"owner_effect_id": effect_id(original.command.key)})
    # Full projection can take seconds. A restart/identity replacement during it
    # invalidates the ephemeral work rather than lending an old boot authority.
    if current_managed_runtime_identity() != runtime_id:
        return None
    if provider is not None and provider.mode_runtime_id != runtime_id:
        return None
    return PreparedDeterminedFeedback(original, qualified, provider, runtime_id)


def _control_effect_in_transaction(database,command,*,clock):
    from app.study.game_feedback import GameFeedback
    from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
    owner = GameFeedback(PostgresGameFeedbackStore(database),None,
        clock=lambda:datetime.fromisoformat(clock.utc_now().replace("Z","+00:00")))
    return owner.control_effect(command)


def _accept_prepared_provider(database, prepared, *, clock, current_runtime_id=None):
    """Only fixed prepared facts and owner SQL run in the borrowed writer."""
    from tempo_legacy_database.adapters.study.game_feedback_authority import current_owner_encounter_intake
    if prepared.require_active_mode:
        if current_runtime_id is None or current_runtime_id != prepared.mode_runtime_id:
            raise DeliveryDeferred("managed_feedback_runtime_identity_changed")
        from tempo_legacy_database.adapters.games.managed_mode import fence_feedback_handoff
        fence_feedback_handoff(database, prepared.command.key.workspace_id,
            runtime_id=current_runtime_id)
    receiver = current_owner_encounter_intake(database,
        retained_owner_capture=prepared.target_capture, snapshot=prepared.target_snapshot,
        prepared_decisions=prepared.prepared_decisions, qualification=prepared.qualification,
        clock=lambda: datetime.fromisoformat(clock.utc_now().replace("Z", "+00:00")))
    return receiver.accept_qualified(prepared.command, qualification=prepared.qualification)


def apply_prepared_determined_feedback(database, prepared, *, clock, current_runtime_id):
    """Apply after the gateway receipt/token fence; caller owns commit.

    The root supplies a fresh trusted boot identity obtained outside this writer,
    never a producer/persisted token. A savepoint rolls back both owners together
    on a stale fence, invalid receipt or failed acknowledgment. No connections,
    filesystem, provider calls or chess preparation occur here.
    """
    from tempo_legacy_database.adapters.games.product_feedback import (
        lock_delivery_in_transaction, save_delivery_in_transaction,
    )
    if current_runtime_id is None or current_runtime_id != prepared.mode_runtime_id:
        return LocalFeedbackProgress(False, True, "managed_feedback_runtime_identity_changed")
    raw = getattr(database, "raw", database)
    if (getattr(database, "read_only", False)
            or raw.info.transaction_status.name != "INTRANS"):
        raise ValueError("borrowed_owner_writer_transaction_required")
    try:
        with raw.transaction():  # A savepoint only; never the caller's commit.
            from tempo_legacy_database.adapters.games.managed_mode import fence_feedback_handoff
            fence_feedback_handoff(database, prepared.delivery.command.key.workspace_id,
                runtime_id=current_runtime_id)
            provider = prepared.provider_feedback
            if provider is not None:
                if (provider.command != prepared.delivery.command
                        or provider.qualification != prepared.delivery.qualification
                        or provider.mode_runtime_id != current_runtime_id
                        or not provider.require_active_mode or not callable(provider.qualification_fence)):
                    raise DeliveryDeferred("first_departure_prepared_authority_required")
                # Mode/config/source locks precede the outbox and Study locks.
                provider.qualification_fence(database, provider.command, provider.qualification)
            current = lock_delivery_in_transaction(database, prepared.delivery.operation_id,
                expected=prepared.original_delivery)
            if current.state != "pending":
                return LocalFeedbackProgress(False, False, "delivery_already_disposed")
            if prepared.is_control:
                receipt = _control_effect_in_transaction(database,prepared.delivery.control,clock=clock)
                saved = save_delivery_in_transaction(database,
                    acknowledge_control_delivery(prepared.delivery,receipt),expected=current)
                return LocalFeedbackProgress(saved.state == "acknowledged",False,saved.error)
            if provider is None:
                save_delivery_in_transaction(database, prepared.delivery, expected=current)
                return LocalFeedbackProgress(False, prepared.delivery.state == "pending",
                    prepared.delivery.qualification_reason)
            receipt = _accept_prepared_provider(database, provider, clock=clock,
                current_runtime_id=current_runtime_id)
            acknowledged = acknowledge_qualified_delivery(prepared.delivery, receipt)
            saved = save_delivery_in_transaction(database, acknowledged, expected=current)
            return LocalFeedbackProgress(saved.state == "acknowledged",
                receipt.status == "pending" or receipt.admission_status == "pending",
                saved.error)
    except Exception as error:
        # The savepoint has already undone any Study acceptance/outbox change.
        _LOGGER.warning("Local Games apply deferred error=%s code=%s",
                        type(error).__name__, _failure_code(error))
        return LocalFeedbackProgress(False, True, "local_feedback_" + _failure_code(error))


def advance_determined_feedback(*, workspace, connections, clock, admitted, limit=1):
    """Standalone mutating worker unit; never a gateway's pure preparer."""
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

    @staticmethod
    def effect_identity(key):
        # Composition consumes the actual Study-owned identity function. Games
        # stores its opaque value; it does not duplicate the hash algorithm.
        from app.study.game_feedback import effect_id
        return effect_id(key)

    def prepare_first_boundary(self,game,*,snapshots,selection):
        from .current_first_boundary import prepare_current_first_boundary
        return prepare_current_first_boundary(game,snapshots=snapshots,selection=selection,
            connections=self.connections)

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
                selector=self.selector, require_active_mode=True)
        _, source, configuration, publications, selector, targets = facts
        return _prepare_qualified_owner(command, qualification, source=source,
            configuration=configuration, publications=publications, selector=selector, capture=self.capture,
            require_active_mode=True, prepared_targets=targets)

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
        except ImportError as error:
            raise DeliveryDeferred("feedback_capability_unavailable") from error
        except (CurrentDecisionUnavailable, ReviewUnavailable, ValueError) as error:
            code = _failure_code(error)
            if code == "unclassified":
                code = "first_departure_current_authority_unavailable"
            raise DeliveryDeferred(code) from error

    def accept_prepared(self, prepared):
        self._require_local_admission()
        runtime_id = None
        if prepared.require_active_mode:
            from .managed_runtime import current_managed_runtime_identity
            runtime_id = current_managed_runtime_identity()
            if runtime_id is None or runtime_id != prepared.mode_runtime_id:
                raise DeliveryDeferred("managed_feedback_runtime_identity_changed")
        with self.connections.writer() as database:
            receipt = _accept_prepared_provider(database, prepared, clock=self.clock,
                current_runtime_id=runtime_id)
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
        self._require_local_admission()
        from .managed_runtime import current_managed_runtime_identity
        from tempo_legacy_database.adapters.games.managed_mode import fence_feedback_handoff
        runtime_id = current_managed_runtime_identity()
        if runtime_id is None:
            raise DeliveryDeferred("managed_feedback_runtime_identity_unavailable")
        with self.connections.writer() as database:
            fence_feedback_handoff(database,command.key.workspace_id,runtime_id=runtime_id)
            return _control_effect_in_transaction(database,command,clock=self.clock)

    def encounter_status(self, key):
        return self.reads.encounter_status(key)

    def effect_status(self, key):
        return self.reads.effect_status(key)

    def accept_lapse(self, command):
        raise ValueError("encounter_worker_does_not_create_legacy_lapses")
