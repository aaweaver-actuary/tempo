"""Explicit exposure command; existing gateway owns the atomic durable receipt."""
from dataclasses import dataclass
from datetime import datetime,timezone
from fastapi import HTTPException
from psycopg.errors import SerializationFailure,LockNotAvailable
from tempo_learning_remediation import RemediationError
from .services.redis_admission_gate import BackgroundAdmissionDeferred
from tempo_learning_review import historical_exposure as policy
from .command_gateway import register_command
from .historical_exposure_contracts import ExposureSelection
from .services.historical_exposure_source import prepare_selection,VerifiedExposure
from .services import legacy_game_authority as source_owner
from .services.historical_exposure_adapter import locked_ports
from .storage.historical_exposure import BorrowedExposureStore,read_record,record_document
from .storage.historical_exposure_facts import lock_card_scope


@dataclass(frozen=True)
class PreparedExposure:
    request: policy.Request
    verified: VerifiedExposure | None


def prepare_request(payload):
    if set(payload)!={'selection'}:raise HTTPException(422,'One explicit exposure selection required')
    try:selection=ExposureSelection.model_validate(payload['selection'])
    except ValueError as error:raise HTTPException(422,'Invalid exposure selection') from error
    # Semantic replay wins even after source edits; it grants no new effects.
    prior=source_owner.read_rows('''SELECT record_json FROM historical_exposure_intents
        WHERE target_id=%s AND workspace_id=%s AND card_id=%s
        AND source_json->'selection'=%s::jsonb LIMIT 2''',
        (selection.target_id,selection.workspace_id,selection.card_id,selection.model_dump_json()))
    if len(prior)>1:raise HTTPException(409,'Ambiguous retained exposure selection')
    if prior:return PreparedExposure(read_record(prior[0]['record_json']).request,None)
    try:verified=prepare_selection(selection)
    except (policy.ExposureRejected,RemediationError) as error:
        raise HTTPException(409,str(error)) from error
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
    try:record=policy.request_priority(store,writer,prepared.request,reader,datetime.now(timezone.utc))
    except policy.ExposureRejected as error:raise HTTPException(409,str(error)) from error
    # The active durable intent is retained. Refresh/completion scheduling is a
    # separately reviewed composition step before this route can be adopted.
    return response(record)


def response(record):
    return dict(encounter_key=record.request.encounter.key(),state=record.state,
        reason=policy.REASON,explanation='Practice requested for an encountered authored line; this is not a forgotten-recall finding.',
        placement=record_document(record)['placement'],completion_review_id=record.completion_review_id)


register_command('historical_exposure.request',request_exposure,prepare=prepare_request)
