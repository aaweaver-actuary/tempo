"""On-demand, background-admitted reads; never a training/review dependency."""
from contextlib import contextmanager
import json
import chess

from fastapi import APIRouter, HTTPException, Query

from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.prefix_reads import diagnostics as storage
from .prefix_diagnostics_contracts import PrefixDiagnosticsList, PrefixDiagnosticsDetail
from .services.activity_gate import activity_gate
from .services.opening_decision_evidence import decision_manifest
from .services.postgres_opening_evidence import _manifest_snapshot
from .services.prefix_diagnostics import ATTEMPT_LIMIT, project_prefix_diagnostics

router = APIRouter()
PAGE_SIZE = 8


def diagnostic_error(message: str, status: int = 409):
    return HTTPException(status, message, headers={'Retry-After': '1'} if status == 503 else None)


@contextmanager
def diagnostic_read():
    if not storage.postgres_is_configured():
        raise diagnostic_error('Prefix difficulty requires the authoritative PostgreSQL service.', 503)
    try:
        with activity_gate.background_request(), activity_gate.background_database_section():
            with storage.diagnostic_connection() as database:
                yield database
    except storage.READ_UNAVAILABLE_ERRORS as error:
        raise diagnostic_error('Prefix difficulty is temporarily unavailable. Retry after study work settles.', 503) from error


def current_generation(database, repertoire_id: str, expected: int | None = None) -> int:
    if storage.repertoire(database, repertoire_id) is None:
        raise diagnostic_error('Repertoire not found.', 404)
    publication = storage.graph_publication(database, repertoire_id)
    if (publication is None or publication['state'] != 'ready'
            or (publication['task_generation'] is not None and
                (publication['task_generation'] > publication['generation'] or publication['task_state'] != 'complete'))):
        raise diagnostic_error('Wait for the repertoire graph to finish publishing, then refresh Prefix difficulty.')
    if expected is not None and publication['generation'] != expected:
        raise diagnostic_error('The repertoire changed. Refresh Prefix difficulty before continuing.')
    return publication['generation']


def prefix_rows(database, repertoire_id: str, generation: int, *, after: str = '', card_id: str | None = None) -> list[dict]:
    return storage.prefix_rows(database, repertoire_id, generation, page_size=PAGE_SIZE, after=after, card_id=card_id)


def prefix_projection(row: dict, repertoire_id: str) -> dict:
    try:
        if row['id'] is None or len(row['colors']) != 1:
            raise ValueError('Presentation or trained color is ambiguous or unavailable')
        snapshot = _manifest_snapshot({**row, 'effective_trained_color': row['colors'][0]})
        manifest = decision_manifest(snapshot, repertoire_id)
        if len(manifest['decisions']) < 2:
            raise ValueError('The published prefix no longer matches this presentation')
        presentation_san = chess.Board(snapshot['start_fen']).variation_san(
            [chess.Move.from_uci(move) for move in json.loads(snapshot['moves_json'])])
        return {'card_id': row['card_id'], 'presentation_san': presentation_san,
                'manifest': manifest, 'unavailable_reason': None}
    except (ValueError, KeyError) as error:
        return {'card_id': row['card_id'], 'presentation_san': None, 'manifest': None,
                'unavailable_reason': f'{error}. Refresh the repertoire or inspect its presentation.'}


@router.get('/api/repertoires/{identifier}/prefix-diagnostics', response_model=PrefixDiagnosticsList)
def prefix_diagnostics_list(identifier: str, after_card_id: str | None = Query(default=None, max_length=200),
                            graph_generation: int | None = Query(default=None, ge=1)):
    if after_card_id is not None and graph_generation is None:
        raise diagnostic_error('Pagination requires its graph generation. Refresh Prefix difficulty.')
    with diagnostic_read() as database:
        generation = current_generation(database, identifier, graph_generation)
        rows = prefix_rows(database, identifier, generation, after=after_card_id or '')
    return {'version': 1, 'repertoire_id': identifier, 'graph_generation': generation,
            'prefixes': [prefix_projection(row, identifier) for row in rows[:PAGE_SIZE]],
            'next_card_id': rows[PAGE_SIZE - 1]['card_id'] if len(rows) > PAGE_SIZE else None}


@router.get('/api/repertoires/{identifier}/prefix-diagnostics/{card_id}', response_model=PrefixDiagnosticsDetail)
def prefix_diagnostics_detail(identifier: str, card_id: str,
                              manifest_id: str = Query(min_length=64, max_length=64),
                              graph_generation: int = Query(ge=1)):
    with diagnostic_read() as database:
        generation = current_generation(database, identifier, graph_generation)
        rows = prefix_rows(database, identifier, generation, card_id=card_id)
        if not rows:
            raise diagnostic_error('This prefix is no longer available. Refresh Prefix difficulty.')
        row = rows[0]
        if row['id'] is None or len(row['colors']) != 1:
            raise diagnostic_error('This prefix has no unambiguous presentation/color. Refresh the repertoire.')
        attempts = [dict(attempt) for attempt in storage.attempt_rows(database, row['id'], identifier, row['colors'][0], card_id, row['revision'], ATTEMPT_LIMIT + 1)]
        observations = [dict(observation) for observation in storage.observation_rows(database, [attempt['attempt_id'] for attempt in attempts[:ATTEMPT_LIMIT]], ATTEMPT_LIMIT * 20)]
    prefix = prefix_projection(row, identifier)
    manifest = prefix['manifest']
    if manifest is None or manifest['manifest_id'] != manifest_id:
        raise diagnostic_error('The prefix presentation changed. Refresh Prefix difficulty before continuing.')
    if any(attempt['manifest_id'] != manifest_id for attempt in attempts[:ATTEMPT_LIMIT]):
        raise diagnostic_error('Stored evidence does not match this presentation. Inspect service diagnostics.')
    try:
        return project_prefix_diagnostics(manifest, generation, attempts,
            [{'attempt_id': record['attempt_id'], 'observation': json.loads(record['observation_json'])} for record in observations])
    except (ValueError, KeyError, IndexError) as error:
        raise diagnostic_error('Stored evidence is invalid. Inspect service diagnostics and retry.') from error
