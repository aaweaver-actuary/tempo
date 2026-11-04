"""Background checkpoint delivery and internal reads for optional shadow facts."""
from fastapi import APIRouter, Header, HTTPException
from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.controls import opening_evidence as storage
from tempo_legacy_database.connections import background_read_connection
from app.contracts.opening_evidence import OpeningEvidenceCheckpoint
from .services.activity_gate import activity_gate
from .services.postgres_opening_evidence import (
    evidence_error,
)

router = APIRouter()


@router.post('/api/opening-evidence/checkpoints')
def opening_evidence_checkpoint(request: OpeningEvidenceCheckpoint,
                                idempotency_key: str | None = Header(default=None, alias='Idempotency-Key')):
    from .command_dispatch import dispatch_command
    if not postgres_store.configured():
        raise evidence_error('Opening shadow evidence requires PostgreSQL', 'opening_evidence_unavailable')
    return dispatch_command('opening_evidence.checkpoint',
                            {'checkpoint':request.model_dump(mode='json')},
                            idempotency_key=idempotency_key, background=True)


@router.get('/api/opening-evidence/attempts/{attempt_id}')
def opening_attempt_evidence(attempt_id: str):
    if not postgres_store.configured():
        raise HTTPException(503, 'Opening shadow evidence requires PostgreSQL')
    import json
    read_section = (background_read_connection(authoritative=True) if activity_gate.in_background
                    else postgres_store.connection(read_only=True))
    with read_section as database:
        attempt = storage.opening_attempt_row(database, attempt_id)
        if not attempt:
            raise HTTPException(404, 'Opening attempt evidence not found')
        attempt = dict(attempt)
        events = storage.opening_event_payloads(database, attempt_id)
    return {**attempt, 'events': [json.loads(event) for event in events]}
