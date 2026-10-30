"""Study-owned durable sweeps, independent of optional producer execution."""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import json

from tempo_learning_remediation import RemediationError
from tempo_legacy_database.connections import background_read_connection, connection
from tempo_legacy_database.adapters.durable_tasks import storage as task_storage
from tempo_legacy_database.adapters.study.game_feedback import (
    PostgresGameFeedbackStore, feedback_preparation_admitted, installed, next_feedback_preparation,
)
from tempo_legacy_database.adapters.study.game_feedback_authority import (
    CurrentDecisionUnavailable, current_owner_encounter_feedback,
)

from app.legacy_game_observation import capture_game
from app.services.durable_tasks import (advance_task_slice_in_transaction, claim_task,
    complete_task_slice_in_transaction, enqueue_task_in_transaction, lock_current_slice)
from app.study.game_feedback import effect_id, instant
from app.services.feedback_diagnostics import feedback_stage

KIND = "study_game_feedback"


@dataclass(frozen=True)
class PreparedFeedbackEffect:
    observed: object
    target_capture: object = None
    target_snapshot: object = None
    qualification: object = None
    prepared_decisions: object = None
    prepared_introduction: object = None
    prepared_priority: object = None
    reason: str | None = None
    retry_at: datetime | None = None
    terminal: bool = False


def prepare_feedback_effect(observed, *, read, capture, clock, qualification_prepare=None):
    """Prepare one retained Study obligation with all connections closed first.

    The same entrypoint covers already-acknowledged priority-only effects and
    foreground pickup. It never calls a provider or waits for a quiet foreground.
    Qualification uses current owner reads, not pending outbox availability.
    """
    feedback_stage('capture')
    command = observed.encounter or observed.lapse
    if command is None:
        return PreparedFeedbackEffect(observed, reason='first_departure_command_unavailable')
    try:
        if qualification_prepare is None:
            from .provider_priority import prepare_feedback_qualification
            prepared = prepare_feedback_qualification(command, read=read, capture=capture, clock=clock,
                determination=(observed.qualification.determined_at if observed.qualification else None),
                stream=(observed.qualification.stream if observed.qualification else None))
        else:
            prepared = qualification_prepare(command)
        if prepared is None:
            return PreparedFeedbackEffect(observed, reason='first_departure_qualification_unavailable',
                retry_at=instant(clock())+timedelta(seconds=30))
        captured, snapshot = prepared.target_capture, prepared.target_snapshot
        qualification, decisions = prepared.qualification, prepared.prepared_decisions
        feedback_stage('prepare')
        from app.services.queue_priority_promotion import prepare_priority_promotion
        from app.services.encounter_preparation import prepare_encounter_introduction
        from app.study.introductions.encounter import PreparedEncounterIntroduction, introduction_target_identity
        target = qualification.selection.decision.model_copy(update={
            'repertoire_revision': decisions.authority.scope_source_revision + 1})
        captured_card = next(row for row in captured['tables']['cards'] if row['id'] == target.presentation_id)
        introduction = (prepare_encounter_introduction(target, read=read)
            if captured_card['introduced_at'] is None else PreparedEncounterIntroduction(
                introduction_target_identity(target), '', (), 'encounter_preparation_required'))
        with read() as database:
            day = PostgresGameFeedbackStore(database).study_day(instant(clock())).isoformat()
        progress = observed.progress
        determined_at = progress.determined_at if progress else qualification.determined_at
        stream = progress.stream if progress else qualification.stream
        priority = prepare_priority_promotion(day,target.presentation_id,read=read,
            priority_order=dict(urgent_determined_at=determined_at,urgent_stream=stream,
                                urgent_effect_id=effect_id(observed.key)))
        return PreparedFeedbackEffect(observed,captured,snapshot,qualification,decisions,introduction,priority)
    except (CurrentDecisionUnavailable, RemediationError, ValueError, ImportError):
        # No raw exception/SQL/PGN is retained in public status. A later sweep
        # can obtain changed source facts without erasing the original debt.
        return PreparedFeedbackEffect(observed, reason='first_departure_qualification_unavailable',
            retry_at=instant(clock())+timedelta(seconds=30))
    except Exception as error:
        reason, retry_after = getattr(error,'reason',None),getattr(error,'retry_after',None)
        if (not isinstance(reason,str) or not reason.replace('_','').isalnum()
                or not isinstance(retry_after,(int,float)) or retry_after < 0):
            raise
        terminal = getattr(error,'retryable',True) is False or reason in {'not_selected_first_departure','first_departure_covered',
            'first_departure_opponent_gap','first_departure_coverage_end',
            'first_departure_missing_own_continuation','first_departure_source_changed',
            'qualification_original_command_mismatch'}
        return PreparedFeedbackEffect(observed,reason=reason,terminal=terminal,
            retry_at=None if terminal else instant(clock())+timedelta(seconds=retry_after))


def apply_prepared_feedback_effect(database, prepared, *, clock, operation_admitted, task_id=None):
    """One bounded writer. Caller supplies actual operation/task admission."""
    if (type(operation_admitted) is not bool or not operation_admitted
            or not feedback_preparation_admitted(database,lock=True,task_id=task_id)):
        raise CurrentDecisionUnavailable('feedback_operation_not_admitted')
    observed = prepared.observed
    if prepared.reason:
        store = PostgresGameFeedbackStore(database)
        store.lock_target(observed.card_id,store.study_day(instant(clock())).isoformat())
        store.lock_effect(observed.key)
        current = store.get(observed.key)
        if current and current.status in {'pending','applied'} and not current.control:
            store.save(replace(current,reason=prepared.reason,admission_status='pending',
                status='held' if prepared.terminal else current.status,
                preparation_retry_at=prepared.retry_at,deferred_reason='authority_unavailable'),instant(clock()))
        return None
    from app.services.queue_admission_adapter import current_owner_encounter_introductions
    from app.study.encounter_feedback import EncounterIntroductionPolicy
    introductions = current_owner_encounter_introductions(database,
        allowance='encounter_exception',paused=not operation_admitted,
        prepared_introduction=prepared.prepared_introduction)
    owner = current_owner_encounter_feedback(database,
        retained_owner_capture=prepared.target_capture,snapshot=prepared.target_snapshot,clock=clock,
        introductions=introductions,introduction_policy=EncounterIntroductionPolicy('encounter_exception'),
        prepared_priority=prepared.prepared_priority,prepared_decisions=prepared.prepared_decisions)
    return owner.refresh_encounter(observed.key,qualification=prepared.qualification)


def _wake_due_refresh(write, clock):
    # Queue task ownership is acquired only after every effect lock has closed.
    from app.services.queue_application import ensure_due_refresh_in_transaction
    from app.services.queue_refresh_wakeup import wake_queue_refresh
    with write() as database:
        day = PostgresGameFeedbackStore(database).study_day(instant(clock())).isoformat()
        ensure_due_refresh_in_transaction(database, day)
    wake_queue_refresh()


def advance_local_feedback(*, read, write, capture, clock, operation_admitted,
                           qualification_prepare=None):
    """Explicit next-card command: prepare/apply at most one local obligation."""
    with read() as database:
        day = PostgresGameFeedbackStore(database).study_day(instant(clock())).isoformat()
        observed = next_feedback_preparation(database,day,now=instant(clock()))
    if observed is None:
        return False
    prepared = prepare_feedback_effect(observed,read=read,capture=capture,clock=clock,
                                      qualification_prepare=qualification_prepare)
    with write() as database:
        receipt = apply_prepared_feedback_effect(database,prepared,clock=clock,
                                                operation_admitted=operation_admitted)
    if receipt is not None and receipt.reason == 'due_queue_refresh_pending':
        _wake_due_refresh(write,clock)
    return receipt is not None and receipt.admission_status == 'available'


def claim_feedback_sweep():
    with background_read_connection() as database:
        if not installed(database) or not PostgresGameFeedbackStore(database).owed_page("", 1):
            return None
    with connection(background=True) as database:
        task = task_storage.task_by_kind(database, (KIND, "current"))
        if task is None or task["state"] not in {"queued", "leased", "retrying"}:
            enqueue_task_in_transaction(database, KIND, "current", {"after": ""}, priority=15)
    return claim_task(KIND)


def execute_feedback_sweep(task, *, read=background_read_connection, write=None,
                           capture=capture_game, clock=None, qualification_prepare=None):
    """One effect and durable cursor per transaction; all reads close first."""
    feedback_stage('read_effect', task)
    write = write or (lambda: connection(background=True))
    clock = clock or (lambda: datetime.now(timezone.utc))
    payload = task.get("payload")
    if payload is None:
        payload = json.loads(task["payload_json"])
    after = payload.get("after", "")
    with read() as database:
        page = PostgresGameFeedbackStore(database).owed_page(after, 1)
    observed = page[0] if page else None
    prepared = (prepare_feedback_effect(observed,read=read,capture=capture,clock=clock,
        qualification_prepare=qualification_prepare) if observed is not None else None)
    due_refresh_needed = False
    feedback_stage('effect_writer')
    with write() as database:
        slice_admitted = lock_current_slice(database, task)
        if not slice_admitted:
            return False
        if observed is None:
            complete_task_slice_in_transaction(database, task)
            return True
        receipt = apply_prepared_feedback_effect(database,prepared,clock=clock,
            operation_admitted=slice_admitted,task_id=task['id'])
        due_refresh_needed = receipt is not None and receipt.reason == 'due_queue_refresh_pending'
        advance_task_slice_in_transaction(database, task, next_phase="scan",
            next_payload={"after": effect_id(observed.key), "hold_reason": prepared.reason})
    if due_refresh_needed:
        feedback_stage('request_due_refresh')
        _wake_due_refresh(write,clock)
    return False
