"""Durable repair preparation and bounded, generation-aware confirmation reads."""
import json
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from . import postgres_store
from .command_gateway import register_command
from .database import connection, read_connection
from .services.integrity_recommendations import admit_recommendation, recommendation_status

router = APIRouter()

class RecommendationRequest(BaseModel):
    signature: str = Field(min_length=1)

register_command('integrity.recommendation.prepare', admit_recommendation)

@router.post('/api/repertoires/{identifier}/integrity/issues/{issue_id}/recommendations')
def prepare_repair_recommendation(identifier: str, issue_id: str, request: RecommendationRequest,
                                  idempotency_key: str | None = Header(None, alias='Idempotency-Key')):
    payload = {'repertoire_id': identifier, 'issue_id': issue_id, 'signature': request.signature}
    if postgres_store.configured():
        from .command_dispatch import dispatch_command
        return dispatch_command('integrity.recommendation.prepare', payload, idempotency_key=idempotency_key)
    with connection() as database:
        result = admit_recommendation(database, payload)
    from .services.game_sync_coordinator import coordinator
    coordinator.wake()
    return result

@router.get('/api/repertoires/{identifier}/integrity/issues/{issue_id}/recommendations')
def read_repair_recommendation(identifier: str, issue_id: str, signature: str):
    return recommendation_status(identifier, issue_id, signature)

@router.get('/api/repertoires/{identifier}/integrity/repairs/{task_id}')
def repair_status(identifier: str, task_id: str, issue_id: str, generation: int = Query(ge=1)):
    with read_connection() as database:
        task = database.execute('SELECT * FROM background_tasks WHERE id=?', (task_id,)).fetchone()
        if (not task or task['kind'] not in {'integrity_repair', 'opening_graph_rebuild'}
                or json.loads(task['payload_json']).get('repertoire_id') != identifier):
            raise HTTPException(404, 'Repair confirmation unavailable. Check Analysis activity before retrying.')
        if task['generation'] < generation:
            raise HTTPException(409, 'Repair generation does not match the saved request')
        state = database.execute('SELECT * FROM repertoire_integrity_state WHERE repertoire_id=?', (identifier,)).fetchone()
        issue = database.execute('SELECT 1 FROM repertoire_integrity_issues WHERE id=? AND repertoire_id=?', (issue_id, identifier)).fetchone()
        count = database.execute('SELECT COUNT(*) FROM repertoire_integrity_issues WHERE repertoire_id=?', (identifier,)).fetchone()[0]
        result = {'task_id': task_id, 'task_generation': task['generation'], 'state': 'waiting',
                  'issue_count': count, 'reason': None, 'retry_task_id': None}
        if task['state'] == 'failed':
            return {**result, 'state': 'failed', 'reason': task['last_error'] or 'Repair processing failed', 'retry_task_id': task_id}
        if state and state['scan_status'] == 'failed':
            scan = state['scan_generation']
            return {**result, 'state': 'failed', 'reason': state['scan_error'] or 'Repertoire validation failed',
                    'retry_task_id': scan.rsplit(':', 1)[0] if postgres_store.configured() and scan else None}
        published = True
        if postgres_store.configured():
            publication = database.execute('SELECT generation,state FROM opening_graph_publications WHERE repertoire_id=?', (identifier,)).fetchone()
            published = bool(publication and publication['state'] == 'ready' and publication['generation'] == task['generation'])
            scan_id, _, scan_generation = (state['scan_generation'] or '').rpartition(':') if state else ('', '', '')
            scan = database.execute('SELECT generation,state,payload_json FROM background_tasks WHERE id=?', (scan_id,)).fetchone()
            published = bool(published and scan and str(scan['generation']) == scan_generation
                             and scan['state'] == 'complete'
                             and json.loads(scan['payload_json']).get('graph_generation') == task['generation'])
        if task['state'] == 'complete' and published and state and state['scan_status'] == 'idle':
            if issue:
                return {**result, 'state': 'failed', 'reason': 'This position still needs repair. Refresh its evidence and choose a response.'}
            return {**result, 'state': 'complete'}
        return result
