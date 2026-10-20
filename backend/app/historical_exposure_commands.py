"""Explicit exposure command; existing gateway owns the atomic durable receipt."""
from dataclasses import dataclass
from datetime import datetime,timezone
from fastapi import HTTPException
from psycopg.errors import SerializationFailure,LockNotAvailable
from tempo_learning_remediation import RemediationError
from .services.redis_admission_gate import BackgroundAdmissionDeferred
from tempo_learning_review import historical_exposure as policy
from .command_gateway import register_command
from .historical_exposure_contracts import ExposureSelection,ExposureControl
from .services.historical_exposure_source import prepare_selection,VerifiedExposure
from .services.historical_exposure_adapter import locked_ports
from .storage.historical_exposure import BorrowedExposureStore,record_document,retained_request_for_selection
from .storage.historical_exposure_facts import lock_card_scope
from .services.historical_exposure_refresh import schedule_refresh


@dataclass(frozen=True)
class PreparedExposure:
    request: policy.Request
    verified: VerifiedExposure | None


def prepare_request(payload):
    if set(payload)!={'selection'}:raise HTTPException(422,'One explicit exposure selection required')
    try:selection=ExposureSelection.model_validate(payload['selection'])
    except ValueError as error:raise HTTPException(422,'Invalid exposure selection') from error
    # Semantic replay wins even after source edits; it grants no new effects.
    try:prior=retained_request_for_selection(selection)
    except policy.ExposureRejected as error:raise HTTPException(409,str(error)) from error
    if prior:return PreparedExposure(prior,None)
    try:verified=prepare_selection(selection)
    except (policy.ExposureRejected,RemediationError) as error:
        raise HTTPException(409,str(error)) from error
    except (ValueError,KeyError) as error:
        raise HTTPException(409,'legacy_capture_invalid') from error
    return PreparedExposure(verified.request,verified)


def request_exposure(database,prepared):
    try:return _request_exposure(database,prepared)
    except (LockNotAvailable,SerializationFailure) as error:
        # Preserve the operation identity and let the existing foreground retry
        # owner yield. Never publish a failed/success receipt for lock contention.
        raise BackgroundAdmissionDeferred('Exposure source is busy; retry the retained operation') from error


def _request_exposure(database,prepared):
    if database.read_only:raise RuntimeError('Exposure command requires caller writer transaction')
    identity=prepared.request.encounter
    scope=policy.CardScope(identity.target_id,identity.workspace_id,identity.card_id)
    lock_card_scope(database,scope)
    prior=BorrowedExposureStore(database,scope).find(identity.key())
    if prior is not None:
        if prior.request!=prepared.request:raise HTTPException(409,'Exposure intent reused')
        return response(prior)
    if prepared.verified is None:raise SerializationFailure('Exposure intent changed during preparation')
    store,writer,reader=locked_ports(database,scope,prepared.verified)
    try:record=policy.request_practice(store,writer,prepared.request,reader,datetime.now(timezone.utc))
    except policy.ExposureRejected as error:raise HTTPException(409,str(error)) from error
    schedule_refresh(database,scope)
    return response(record)


def response(record):
    return dict(encounter_key=record.request.encounter.key(),state=record.state,
        placement_mode='ordinary_queue',explanation='Practice requested for an encountered authored line; this is not a forgotten-recall finding.',
        last_admission={**record_document(record)['placement'],'queue_day':record.queue_day},
        completion_review_id=record.completion_review_id)


register_command('historical_exposure.request',request_exposure,prepare=prepare_request)


def prepare_control(payload):
    try:return ExposureControl.model_validate(payload)
    except ValueError as error:raise HTTPException(422,'Invalid retained exposure control') from error


def control_exposure(database,control,*,cancel):
    scope=policy.CardScope(control.target_id,control.workspace_id,control.card_id)
    try:
        lock_card_scope(database,scope)
        record=BorrowedExposureStore(database,scope).find(control.encounter_key)
        if record is None:raise HTTPException(404,'Retained exposure not found in this card scope')
        if not cancel:
            if record.state=='pending':schedule_refresh(database,scope)
            return {**response(record),'refresh_requested':record.state=='pending'}
        record=policy.cancel_request(BorrowedExposureStore(database,scope),record,datetime.now(timezone.utc))
        return response(record)
    except (LockNotAvailable,SerializationFailure) as error:
        raise BackgroundAdmissionDeferred('Exposure source busy; retain control operation') from error


def cancel_exposure(database,control):return control_exposure(database,control,cancel=True)
def refresh_exposure(database,control):return control_exposure(database,control,cancel=False)


register_command('historical_exposure.cancel',cancel_exposure,prepare=prepare_control)
register_command('historical_exposure.refresh',refresh_exposure,prepare=prepare_control)
