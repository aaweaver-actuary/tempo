"""Background checkpoint delivery and internal reads for optional shadow facts."""
from fastapi import APIRouter, Header, HTTPException, Query
from . import postgres_store
from .command_gateway import register_command
from .database import background_read_connection
from .opening_evidence_contracts import OpeningEvidenceCheckpoint
from .services.activity_gate import activity_gate
from .services.postgres_opening_evidence import (
    decision_evidence, evidence_error, prepare_standalone_checkpoint, commit_standalone_checkpoint,
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


@router.get('/api/opening-evidence/decisions/{decision_id}')
def opening_decision_evidence(decision_id: str, offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100)):
    if not postgres_store.configured():
        raise HTTPException(503, 'Opening shadow evidence requires PostgreSQL')
    with postgres_store.connection(read_only=True) as database:
        return decision_evidence(database, decision_id, offset=offset, limit=limit)


register_command('opening_evidence.checkpoint', commit_standalone_checkpoint, prepare=prepare_standalone_checkpoint)


@router.get('/api/opening-evidence/attempts/{attempt_id}')
def opening_attempt_evidence(attempt_id: str):
    if not postgres_store.configured():
        raise HTTPException(503, 'Opening shadow evidence requires PostgreSQL')
    import json
    read_section = (background_read_connection(authoritative=True) if activity_gate.in_background
                    else postgres_store.connection(read_only=True))
    with read_section as database:
        attempt = database.execute_native('SELECT * FROM opening_evidence_attempts WHERE attempt_id=%s', (attempt_id,)).fetchone()
        if not attempt:
            raise HTTPException(404, 'Opening attempt evidence not found')
        attempt = dict(attempt)
        events = [row[0] for row in database.execute_native('SELECT event_json FROM opening_evidence_events WHERE attempt_id=%s ORDER BY sequence LIMIT 256', (attempt_id,)).fetchall()]
    return {**attempt, 'events': [json.loads(event) for event in events]}
