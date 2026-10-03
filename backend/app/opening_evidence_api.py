"""Background checkpoint delivery and internal reads for optional shadow facts."""
from fastapi import APIRouter, Header, HTTPException, Query
from . import postgres_store
from .command_gateway import register_command
from .opening_evidence_contracts import OpeningEvidenceCheckpoint
from .services.postgres_opening_evidence import decision_evidence, persist_checkpoint, prepare_checkpoint

router = APIRouter()


@router.post('/api/opening-evidence/checkpoints')
def opening_evidence_checkpoint(request: OpeningEvidenceCheckpoint,
                                idempotency_key: str | None = Header(default=None, alias='Idempotency-Key')):
    from .command_dispatch import dispatch_command
    return dispatch_command('opening_evidence.checkpoint',
                            {'checkpoint':request.model_dump(mode='json'), 'prepared_manifest':prepare_checkpoint(request)},
                            idempotency_key=idempotency_key, background=True)


@router.get('/api/opening-evidence/decisions/{decision_id}')
def opening_decision_evidence(decision_id: str, offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100)):
    if not postgres_store.configured():
        raise HTTPException(503, 'Opening shadow evidence requires PostgreSQL')
    with postgres_store.connection(read_only=True) as database:
        return decision_evidence(database, decision_id, offset=offset, limit=limit)


register_command('opening_evidence.checkpoint', persist_checkpoint)


@router.get('/api/opening-evidence/attempts/{attempt_id}')
def opening_attempt_evidence(attempt_id: str):
    if not postgres_store.configured():
        raise HTTPException(503, 'Opening shadow evidence requires PostgreSQL')
    import json
    with postgres_store.connection(read_only=True) as database:
        attempt = database.execute_native('SELECT * FROM opening_evidence_attempts WHERE attempt_id=%s', (attempt_id,)).fetchone()
        if not attempt:
            raise HTTPException(404, 'Opening attempt evidence not found')
        events = database.execute_native('SELECT event_json FROM opening_evidence_events WHERE attempt_id=%s ORDER BY sequence LIMIT 256', (attempt_id,)).fetchall()
        return {**dict(attempt), 'events': [json.loads(row[0]) for row in events]}
