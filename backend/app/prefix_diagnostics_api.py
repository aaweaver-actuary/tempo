"""On-demand, background-admitted reads; never a training/review dependency."""
from contextlib import contextmanager
import json
import chess

from fastapi import APIRouter, HTTPException, Query
import psycopg
from psycopg_pool import PoolTimeout
import redis

from . import postgres_store
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
    if not postgres_store.configured():
        raise diagnostic_error('Prefix difficulty requires the authoritative PostgreSQL service.', 503)
    try:
        with activity_gate.background_request(), activity_gate.background_database_section():
            with postgres_store.connection(read_only=True, background=True, repeatable_read=True) as database:
                yield database
    except (psycopg.OperationalError, psycopg.errors.QueryCanceled,
            psycopg.errors.LockNotAvailable, PoolTimeout, redis.RedisError) as error:
        raise diagnostic_error('Prefix difficulty is temporarily unavailable. Retry after study work settles.', 503) from error


def current_generation(database, repertoire_id: str, expected: int | None = None) -> int:
    if database.execute_native('SELECT id FROM repertoires WHERE id=%s', (repertoire_id,)).fetchone() is None:
        raise diagnostic_error('Repertoire not found.', 404)
    publication = database.execute_native(
        "SELECT publication.generation,publication.state,task.generation task_generation,task.state task_state "
        "FROM opening_graph_publications publication LEFT JOIN background_tasks task "
        "ON task.kind='opening_graph_rebuild' AND task.deduplication_key=publication.repertoire_id "
        "WHERE publication.repertoire_id=%s", (repertoire_id,),
    ).fetchone()
    if (publication is None or publication['state'] != 'ready'
            or (publication['task_generation'] is not None and
                (publication['task_generation'] > publication['generation'] or publication['task_state'] != 'complete'))):
        raise diagnostic_error('Wait for the repertoire graph to finish publishing, then refresh Prefix difficulty.')
    if expected is not None and publication['generation'] != expected:
        raise diagnostic_error('The repertoire changed. Refresh Prefix difficulty before continuing.')
    return publication['generation']


def prefix_rows(database, repertoire_id: str, generation: int, *, after: str = '', card_id: str | None = None) -> list[dict]:
    # Indexed, keyset-paged current prefix IDs first; no repertoire traversal.
    selection = 'step.card_id=%s' if card_id is not None else 'step.card_id>%s'
    rows = database.execute_native(
        "WITH prefix_ids AS MATERIALIZED (SELECT DISTINCT step.card_id FROM opening_graph_steps step "
        "JOIN cards card ON card.id=step.card_id "
        "WHERE step.repertoire_id=%s AND step.generation=%s AND step.segment_kind='prefix' "
        "AND step.last_decision_index>step.first_decision_index AND " + selection +
        " AND card.archived=0 AND card.content_type='opening' "
        "AND (card.repertoire_id=%s OR EXISTS(SELECT 1 FROM repertoire_cards link "
        "WHERE link.repertoire_id=%s AND link.card_id=card.id)) "
        "AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block "
        "WHERE block.repertoire_id=%s AND block.card_id=card.id) ORDER BY step.card_id LIMIT %s) "
        "SELECT card.id card_id,card.revision,card.start_fen,card.moves_json,card.trained_color,snapshot.id, "
        "ARRAY(SELECT DISTINCT step.trained_color FROM opening_graph_steps step "
        "WHERE step.repertoire_id=%s AND step.generation=%s AND step.card_id=card.id "
        "AND step.segment_kind='prefix' AND step.last_decision_index>step.first_decision_index "
        "AND step.starting_fen=card.start_fen AND step.moves_json::jsonb=card.moves_json::jsonb "
        "ORDER BY step.trained_color LIMIT 2) colors "
        "FROM prefix_ids JOIN cards card ON card.id=prefix_ids.card_id "
        "LEFT JOIN opening_evidence_presentations snapshot ON snapshot.card_id=card.id "
        "AND snapshot.revision=card.revision AND snapshot.start_fen=card.start_fen "
        "AND snapshot.moves_json=card.moves_json AND snapshot.trained_color IS NOT DISTINCT FROM card.trained_color "
        "ORDER BY card.id",
        (repertoire_id, generation, card_id if card_id is not None else after,
         repertoire_id, repertoire_id, repertoire_id, 1 if card_id is not None else PAGE_SIZE + 1,
         repertoire_id, generation),
    ).fetchall()
    return [dict(row) for row in rows]


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
        attempts = [dict(attempt) for attempt in database.execute_native(
            "SELECT attempt_id,manifest_id,state,started_at FROM opening_evidence_attempts "
            "WHERE presentation_snapshot_id=%s AND repertoire_id=%s AND trained_color=%s "
            "AND card_id=%s AND card_revision=%s ORDER BY started_at DESC,attempt_id DESC LIMIT %s",
            (row['id'], identifier, row['colors'][0], card_id, row['revision'], ATTEMPT_LIMIT + 1),
        ).fetchall()]
        observations = [dict(observation) for observation in database.execute_native(
            "SELECT attempt_id,decision_index,observation_json FROM opening_evidence_observations "
            "WHERE attempt_id=ANY(%s::text[]) ORDER BY attempt_id,decision_index LIMIT %s",
            ([attempt['attempt_id'] for attempt in attempts[:ATTEMPT_LIMIT]], ATTEMPT_LIMIT * 20),
        ).fetchall()]
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
