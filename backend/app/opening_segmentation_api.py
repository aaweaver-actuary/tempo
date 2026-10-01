"""Read-only advisory API and durable presentation-preference commands."""
from datetime import datetime, timezone
import json
from typing import Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from . import postgres_store
from .command_gateway import register_command
from .database import read_connection
from .services.opening_segmentation import RECOMMENDATION_VERSION, POLICY_VERSION, stable_key
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
    if not postgres_store.configured():
        raise HTTPException(409, 'Recommended segmentation requires the PostgreSQL product. Practice remains available.')


def state_projection(database, repertoire_id: str) -> dict:
    if not database.execute_native('SELECT 1 FROM repertoires WHERE id=%s', (repertoire_id,)).fetchone():
        raise HTTPException(404, 'Repertoire not found')
    row = database.execute_native(
        "SELECT state.*,task.state task_state,task.last_error FROM opening_segmentation_state state "
        "LEFT JOIN background_tasks task ON task.kind='opening_segmentation' AND task.deduplication_key=state.repertoire_id "
        "WHERE state.repertoire_id=%s", (repertoire_id,),
    ).fetchone()
    if row is None:
        return {'state': 'unavailable', 'content_version': 0, 'graph_generation': None, 'run_id': None, 'error': None}
    state = dict(row)
    state['error'] = state.pop('last_error') if state.pop('task_state') == 'failed' else None
    if state['error']:
        state['state'] = 'failed'
    return state


def source_fingerprint(database, run_id: str, recommendation_id: str) -> str:
    return database.execute_native(
        "SELECT source_fingerprint FROM opening_segmentation_recommendations WHERE run_id=%s AND id=%s", (run_id, recommendation_id),
    ).fetchone()[0]


def recommendation_snapshot(state: dict, recommendation: dict) -> str:
    return stable_key('opening-segmentation-snapshot', RECOMMENDATION_VERSION, POLICY_VERSION,
                      recommendation['repertoire_id'], recommendation['id'], state['run_id'],
                      state['content_version'], state['graph_generation'], recommendation['source_fingerprint'])


def recommendation_projection(state: dict, recommendation) -> dict:
    projected = {key: value for key, value in dict(recommendation).items() if key != 'run_id'}
    return {**projected, 'snapshot_id': recommendation_snapshot(state, projected)}


def consistent_preview_read(database):
    # First statement: all bounded reads see one publication, even during a concurrent rebuild.
    database.execute_native('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')


@router.get('/api/repertoires/{identifier}/segmentation')
def segmentation_list(identifier: str):
    require_postgres()
    with read_connection() as database:
        consistent_preview_read(database)
        state = state_projection(database, identifier)
        rows = database.execute_native(
            "SELECT recommendation.* FROM opening_segmentation_recommendations recommendation "
            "LEFT JOIN opening_segmentation_preferences preference ON preference.repertoire_id=recommendation.repertoire_id "
            "AND preference.recommendation_id=recommendation.id "
            "AND preference.source_fingerprint=recommendation.source_fingerprint "
            "WHERE recommendation.run_id=%s AND preference.recommendation_id IS NULL "
            "ORDER BY decisions_avoided DESC,additional_starts,segment_count,id LIMIT 20", (state['run_id'],),
        ).fetchall() if state['state'] == 'ready' else []
        invalidated_pins = database.execute_native(
            "SELECT COUNT(*) FROM opening_segmentation_preferences preference WHERE preference.repertoire_id=%s "
            "AND preference.choice='keep_current' AND NOT EXISTS(SELECT 1 FROM opening_segmentation_recommendations recommendation "
            "WHERE recommendation.run_id=%s AND recommendation.id=preference.recommendation_id "
            "AND recommendation.source_fingerprint=preference.source_fingerprint)",
            (identifier, state['run_id']),
        ).fetchone()[0] if state['state'] == 'ready' else 0
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
        recommendation = database.execute_native(
            'SELECT * FROM opening_segmentation_recommendations WHERE run_id=%s AND id=%s',
            (state['run_id'], recommendation_id),
        ).fetchone()
        if recommendation is None:
            raise HTTPException(409 if snapshot_id else 404, 'Recommendation not found in the current source. Refresh the preview.')
        snapshot = recommendation_snapshot(state, dict(recommendation))
        if snapshot_id is not None and snapshot_id != snapshot:
            raise HTTPException(409, 'This preview snapshot changed. Refresh the recommendation before continuing.')
        parts = database.execute_native(
            'SELECT segment_id,segment_json FROM opening_segmentation_parts WHERE run_id=%s AND recommendation_id=%s '
            'AND segment_id>%s ORDER BY segment_id LIMIT 9', (state['run_id'], recommendation_id, after_segment or ''),
        ).fetchall()
        routes = database.execute_native(
            "SELECT DISTINCT line.id,line.name FROM opening_segmentation_sources source "
            "JOIN opening_graph_steps step ON step.card_id=source.card_id AND step.repertoire_id=%s AND step.generation=%s "
            "JOIN repertoire_lines line ON line.id=step.line_id "
            "WHERE source.run_id=%s AND source.recommendation_id=%s AND line.id>%s ORDER BY line.id LIMIT 9",
            (identifier, state['graph_generation'], state['run_id'], recommendation_id, after_route or ''),
        ).fetchall()
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
    database.execute_native('SELECT repertoire_id FROM opening_segmentation_state WHERE repertoire_id=%s FOR UPDATE', (repertoire_id,))
    state = state_projection(database, repertoire_id)
    if (state['state'] != 'ready' or state['content_version'] != request.content_version
            or state['graph_generation'] != request.graph_generation):
        raise HTTPException(409, 'The repertoire changed. Refresh the recommendation before saving this choice.')
    recommendation = database.execute_native(
        'SELECT * FROM opening_segmentation_recommendations WHERE run_id=%s AND id=%s',
        (state['run_id'], payload['recommendation_id']),
    ).fetchone()
    if recommendation is None:
        raise HTTPException(409, 'This recommendation is no longer current')
    if request.snapshot_id is not None and request.snapshot_id != recommendation_snapshot(state, dict(recommendation)):
        raise HTTPException(409, 'This preview snapshot changed. Refresh before saving this choice.')
    if source_fingerprint(database, state['run_id'], payload['recommendation_id']) != request.source_fingerprint:
        raise HTTPException(409, 'The recommendation sources changed. Refresh before saving.')
    database.execute_native(
        'INSERT INTO opening_segmentation_preferences(repertoire_id,recommendation_id,choice,source_fingerprint,updated_at) '
        'VALUES(%s,%s,%s,%s,%s) ON CONFLICT(repertoire_id,recommendation_id) DO UPDATE SET '
        'choice=excluded.choice,source_fingerprint=excluded.source_fingerprint,updated_at=excluded.updated_at',
        (repertoire_id, payload['recommendation_id'], request.choice, request.source_fingerprint, datetime.now(timezone.utc).isoformat()),
    )
    return {'saved': True, 'choice': request.choice}


def refresh_segmentation(database, payload: dict) -> dict:
    repertoire_id = payload['repertoire_id']
    graph = database.execute_native(
        "SELECT graph.generation FROM opening_graph_publications graph JOIN repertoire_integrity_state integrity "
        "ON integrity.repertoire_id=graph.repertoire_id WHERE graph.repertoire_id=%s AND integrity.scan_status='idle' "
        "AND integrity.status IN ('clean','needs_repair')", (repertoire_id,),
    ).fetchone()
    if graph is None:
        raise HTTPException(409, 'Wait for repertoire preparation and integrity checks, then retry.')
    # A failed run is retried through a new durable generation; ordinary ready reads stay cached.
    database.execute_native(
        "UPDATE opening_segmentation_state SET state='stale' WHERE repertoire_id=%s AND state='building' "
        "AND EXISTS(SELECT 1 FROM background_tasks WHERE kind='opening_segmentation' AND deduplication_key=%s AND state='failed')",
        (repertoire_id, repertoire_id),
    )
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
        'recommendation_id': recommendation_id, 'request': request.model_dump()}, idempotency_key=idempotency_key)


register_command('opening.segmentation.refresh', refresh_segmentation)
register_command('opening.segmentation.preference', save_preference)
