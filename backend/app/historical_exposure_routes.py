"""One explicit operator-only practice request; no direct HTTP database writes."""
import hmac
import os
from pathlib import Path
import stat
from fastapi import APIRouter, Depends, Header, HTTPException

from . import postgres_store
from .command_dispatch import dispatch_command
from .historical_exposure_contracts import ExposureSelection,ExposureControl

router=APIRouter()


def require_exposure_operator(authorization: str | None=Header(default=None)):
    location=os.environ.get('TEMPO_EXPOSURE_OPERATOR_TOKEN_FILE')
    if not location:raise HTTPException(503,'Historical exposure operator is not configured')
    try:
        path=Path(location)
        mode=path.stat().st_mode
        if not stat.S_ISREG(mode) or stat.S_IMODE(mode)&0o077 or path.stat().st_size>4096:
            raise ValueError('private credential required')
        credential=path.read_text().strip()
        if len(credential)<32:raise ValueError('private credential required')
    except (OSError,ValueError):
        raise HTTPException(503,'Historical exposure operator is not configured') from None
    supplied=authorization[7:] if authorization and authorization.startswith('Bearer ') else ''
    if not hmac.compare_digest(supplied.encode(),credential.encode()):
        raise HTTPException(401,'Operator authorization required')


@router.post('/api/learning/historical-exposure',dependencies=[Depends(require_exposure_operator)])
def request_historical_exposure(selection: ExposureSelection,
    idempotency_key: str=Header(alias='Idempotency-Key',min_length=1,max_length=128)):
    if not postgres_store.configured():
        raise HTTPException(404,'Historical exposure requires the PostgreSQL owner')
    return dispatch_command('historical_exposure.request',{'selection':selection.model_dump(mode='json')},
        idempotency_key=idempotency_key)


@router.post('/api/learning/historical-exposure/cancel',dependencies=[Depends(require_exposure_operator)])
def cancel_historical_exposure(control: ExposureControl,
    idempotency_key: str=Header(alias='Idempotency-Key',min_length=1,max_length=128)):
    if not postgres_store.configured():raise HTTPException(404,'Historical exposure requires the PostgreSQL owner')
    return dispatch_command('historical_exposure.cancel',control.model_dump(mode='json'),idempotency_key=idempotency_key)


@router.post('/api/learning/historical-exposure/refresh',dependencies=[Depends(require_exposure_operator)])
def refresh_historical_exposure(control: ExposureControl,
    idempotency_key: str=Header(alias='Idempotency-Key',min_length=1,max_length=128)):
    if not postgres_store.configured():raise HTTPException(404,'Historical exposure requires the PostgreSQL owner')
    return dispatch_command('historical_exposure.refresh',control.model_dump(mode='json'),idempotency_key=idempotency_key)


@router.post('/api/learning/historical-exposure/status',dependencies=[Depends(require_exposure_operator)])
def historical_practice_status(control: ExposureControl):
    if not postgres_store.configured():raise HTTPException(404,'Historical practice requires the PostgreSQL owner')
    from tempo_learning_review.historical_exposure import CardScope
    from .storage.historical_exposure import read_status
    snapshot=read_status(CardScope(control.target_id,control.workspace_id,control.card_id),control.encounter_key)
    if snapshot is None:raise HTTPException(404,'Retained practice request not found in this scope')
    record,retry_state=snapshot
    return dict(state=record.state,last_admission=dict(status=record.placement.status,reason=record.placement.reason,
        queue_entry_id=record.placement.queue_entry_id,queue_day=record.queue_day,mode=record.placement.mode),completion_review_id=record.completion_review_id,
        retry_state=retry_state,retry_required=record.state=='pending' and retry_state in ('failed','not_scheduled'))
