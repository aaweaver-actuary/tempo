"""Read-only advisory API and durable presentation-preference commands."""
from datetime import datetime, timezone
import json
from typing import Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.authored_repertoires.opening_advisory import segmentation as storage
from .command_gateway import register_command
from tempo_legacy_database.connections import read_connection
from app.chess.opening_segments import RECOMMENDATION_VERSION, POLICY_VERSION, stable_key
from .services.postgres_opening_segmentation import request_segmentation_in_transaction

router = APIRouter()


class SegmentationPreference(BaseModel):
    content_version: int = Field(ge=0)
    graph_generation: int = Field(ge=1)
    source_fingerprint: str = Field(min_length=1)
    choice: Literal['dismissed', 'keep_current']
    # Existing durable commands retain their source checks; new clients bind the publication too.
    snapshot_id: str | None = Field(default=None, min_length=1)


def require_postgres():
    if not storage.postgres_is_configured():
        raise HTTPException(409, 'Recommended segmentation requires the PostgreSQL product. Practice remains available.')


def state_projection(database, repertoire_id: str) -> dict:
    if not storage.repertoire(database, repertoire_id):
        raise HTTPException(404, 'Repertoire not found')
    row = storage.state_row(database, repertoire_id)
    if row is None:
        return {'state': 'unavailable', 'content_version': 0, 'graph_generation': None, 'run_id': None, 'error': None}
    state = dict(row)
    state['error'] = state.pop('last_error') if state.pop('task_state') == 'failed' else None
    if state['error']:
        state['state'] = 'failed'
    return state


def source_fingerprint(database, run_id: str, recommendation_id: str) -> str:
    return storage.source_fingerprint(database, run_id, recommendation_id)


def recommendation_snapshot(state: dict, recommendation: dict) -> str:
    return stable_key('opening-segmentation-snapshot', RECOMMENDATION_VERSION, POLICY_VERSION,
                      recommendation['repertoire_id'], recommendation['id'], state['run_id'],
                      state['content_version'], state['graph_generation'], recommendation['source_fingerprint'])


def recommendation_projection(state: dict, recommendation) -> dict:
    projected = {key: value for key, value in dict(recommendation).items() if key != 'run_id'}
    return {**projected, 'snapshot_id': recommendation_snapshot(state, projected)}


def consistent_preview_read(database):
    # First statement: all bounded reads see one publication, even during a concurrent rebuild.
    storage.consistent_preview_read(database)


@router.get('/api/repertoires/{identifier}/segmentation')
def segmentation_list(identifier: str):
    require_postgres()
    with read_connection() as database:
        consistent_preview_read(database)
        state = state_projection(database, identifier)
        rows = storage.visible_recommendations(database, state['run_id']) if state['state'] == 'ready' else []
        invalidated_pins = storage.invalidated_pin_count(database, identifier, state['run_id']) if state['state'] == 'ready' else 0
    return {'version': RECOMMENDATION_VERSION, 'preview_only': True, 'state': state['state'],
            'content_version': state['content_version'], 'graph_generation': state['graph_generation'],
            'error': state['error'], 'invalidated_pins': invalidated_pins,
            'recommendations': [recommendation_projection(state, row) for row in rows]}


@router.get('/api/repertoires/{identifier}/segmentation/{recommendation_id}')
def segmentation_detail(identifier: str, recommendation_id: str, after_segment: str | None = None, after_route: str | None = None, snapshot_id: str | None = None):
    require_postgres()
    if (after_segment is not None or after_route is not None) and not snapshot_id:
        raise HTTPException(409, 'Pagination requires its preview snapshot. Refresh the recommendation and retry.')
    with read_connection() as database:
        consistent_preview_read(database)
        state = state_projection(database, identifier)
        if state['state'] != 'ready':
            raise HTTPException(409, 'The repertoire changed. Refresh recommended segmentation.')
        recommendation = storage.recommendation(database, state['run_id'], recommendation_id)
        if recommendation is None:
            raise HTTPException(409 if snapshot_id else 404, 'Recommendation not found in the current source. Refresh the preview.')
        snapshot = recommendation_snapshot(state, dict(recommendation))
        if snapshot_id is not None and snapshot_id != snapshot:
            raise HTTPException(409, 'This preview snapshot changed. Refresh the recommendation before continuing.')
        parts = storage.segment_rows(database, state['run_id'], recommendation_id, after_segment or '')
        routes = storage.source_route_rows(database, identifier, state['graph_generation'], state['run_id'], recommendation_id, after_route or '')
        fingerprint = source_fingerprint(database, state['run_id'], recommendation_id)
    kind = recommendation['kind']
    return {'version': RECOMMENDATION_VERSION, 'preview_only': True, 'content_version': state['content_version'],
            'graph_generation': state['graph_generation'], 'source_fingerprint': fingerprint, 'snapshot_id': snapshot,
            'recommendation': recommendation_projection(state, recommendation),
            'rationale': ('These lines share moves before branching. Practice the shared opening once, then review the branches separately.'
                          if kind == 'shared_trunk' else 'Different move orders reach a compatible continuation. Keep the incoming routes and share the continuation.'),
            'estimate_basis': 'structural learner-decision count; not a time or learning estimate',
            'segments': [json.loads(row[1]) for row in parts[:8]],
            'next_segment': parts[7][0] if len(parts) > 8 else None,
            'routes': [dict(row) for row in routes[:8]], 'next_route': routes[7][0] if len(routes) > 8 else None}


def save_preference(database, payload: dict) -> dict:
    request = SegmentationPreference.model_validate(payload['request'])
    repertoire_id = payload['repertoire_id']
    storage.lock_state(database, repertoire_id)
    state = state_projection(database, repertoire_id)
    if (state['state'] != 'ready' or state['content_version'] != request.content_version
            or state['graph_generation'] != request.graph_generation):
        raise HTTPException(409, 'The repertoire changed. Refresh the recommendation before saving this choice.')
    recommendation = storage.recommendation(database, state['run_id'], payload['recommendation_id'])
    if recommendation is None:
        raise HTTPException(409, 'This recommendation is no longer current')
    if request.snapshot_id is not None and request.snapshot_id != recommendation_snapshot(state, dict(recommendation)):
        raise HTTPException(409, 'This preview snapshot changed. Refresh before saving this choice.')
    if source_fingerprint(database, state['run_id'], payload['recommendation_id']) != request.source_fingerprint:
        raise HTTPException(409, 'The recommendation sources changed. Refresh before saving.')
    storage.upsert_preference(database, (repertoire_id, payload['recommendation_id'], request.choice, request.source_fingerprint, datetime.now(timezone.utc).isoformat()))
    return {'saved': True, 'choice': request.choice}


def refresh_segmentation(database, payload: dict) -> dict:
    repertoire_id = payload['repertoire_id']
    graph = storage.prepared_graph(database, repertoire_id)
    if graph is None:
        raise HTTPException(409, 'Wait for repertoire preparation and integrity checks, then retry.')
    # A failed run is retried through a new durable generation; ordinary ready reads stay cached.
    storage.invalidate_failed_build(database, repertoire_id)
    return request_segmentation_in_transaction(database, repertoire_id, int(graph[0]))


@router.post('/api/repertoires/{identifier}/segmentation/refresh')
def request_refresh(identifier: str, idempotency_key: str | None = Header(default=None, alias='Idempotency-Key')):
    require_postgres()
    from .command_dispatch import dispatch_command
    return dispatch_command('opening.segmentation.refresh', {'repertoire_id': identifier}, idempotency_key=idempotency_key)


@router.post('/api/repertoires/{identifier}/segmentation/{recommendation_id}/preference')
def request_preference(identifier: str, recommendation_id: str, request: SegmentationPreference,
                       idempotency_key: str | None = Header(default=None, alias='Idempotency-Key')):
    require_postgres()
    from .command_dispatch import dispatch_command
    return dispatch_command('opening.segmentation.preference', {'repertoire_id': identifier,
        'recommendation_id': recommendation_id, 'request': request.model_dump(exclude_none=True)}, idempotency_key=idempotency_key)


register_command('opening.segmentation.refresh', refresh_segmentation)
register_command('opening.segmentation.preference', save_preference)
