"""Reader-only HTTP dispatch for the durable foreground transition command."""
from fastapi import APIRouter, Header, HTTPException

from . import postgres_store
from .command_dispatch import dispatch_command
from .prefix_transition_contracts import PrefixTransitionApplyRequest, PrefixTransitionApplicationResult, PrefixTransitionPendingResponse

router = APIRouter()


@router.post('/api/repertoires/{identifier}/prefix-transition/apply',
             response_model=PrefixTransitionApplicationResult, response_model_exclude_none=True,
             responses={202: {'model': PrefixTransitionPendingResponse,
                              'description': 'Accepted delivery; follow the durable operation until publication completes.',
                              'headers': {'Location': {'schema': {'type': 'string'}, 'description': 'Existing operation status URL'}}},
                        409: {'description': 'Stale, blocked, conflicting or unsupported application'},
                        503: {'description': 'Command transport unavailable; retry the same identity'}})
def prefix_transition_apply(identifier: str, request: PrefixTransitionApplyRequest,
                            idempotency_key: str = Header(alias='Idempotency-Key', min_length=1, max_length=128)):
    if not postgres_store.configured():
        raise HTTPException(409, {'code': 'unsupported_backend', 'message': 'Prefix application requires authoritative PostgreSQL.'})
    return dispatch_command('repertoire.prefix_transition.apply',
                            {'operation_id': idempotency_key, 'repertoire_id': identifier, 'request': request.model_dump(mode='json')},
                            idempotency_key=idempotency_key, wait_seconds=0, background=False)
