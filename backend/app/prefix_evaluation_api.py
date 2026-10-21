"""Explicit, bounded PostgreSQL diagnostic reads without commands or projections."""
from collections import Counter
from contextlib import contextmanager
import json
from time import monotonic

from fastapi import APIRouter, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from tempo_legacy_database.adapters.authored_repertoires.opening_advisory.evaluation import psycopg, PoolTimeout, redis

from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.authored_repertoires.opening_advisory import evaluation as storage
from .prefix_evaluation_contracts import PrefixEvaluationRequest, PrefixEvaluationResponse, SourceResponse
from .services.activity_gate import activity_gate
from .services import redis_admission_gate
from .services.opening_graph import GraphStep
from .services.prefix_evaluation import (
    EVALUATION_VERSION, ESTIMATE_BASIS, MAX_SOURCE_LINES, MAX_GRAPH_STEPS,
    EvaluationSnapshot, SourceLine, SplitOverride, PublishedCard, PrefixEvaluationError,
    iter_prefix_evaluation, snapshot_identity, validate_source,
)

MAX_SOURCE_BYTES = 4 * 1024 * 1024
COMPUTATION_SECONDS = 10


class PrefixEvaluationForegroundDeferred(HTTPException):
    """Keep the public retry response distinct from an execution deadline."""

    def __init__(self):
        super().__init__(503, {'code': 'evaluation_busy',
                              'message': 'Study work is active. Retry the diagnostic when study is idle.'},
                         headers={'Retry-After': '1'})


def diagnostic_error(code, message, status=409):
    return HTTPException(status, {'code': code, 'message': message},
                         headers={'Retry-After': '1'} if status == 503 else None)


class DiagnosticRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def diagnostic_handler(request):
            try: return await handler(request)
            except RequestValidationError as error:
                raise diagnostic_error('invalid_selection', 'Provide a snapshot, source line IDs, and integer depths from 1 to 20.', 422) from error
        return diagnostic_handler


router = APIRouter(route_class=DiagnosticRoute)


def check_available(deadline):
    if monotonic() >= deadline:
        raise diagnostic_error('evaluation_busy', 'Evaluation exceeded its computation deadline. Retry when study is idle.', 503)
    if activity_gate.foreground_waiting or (redis_admission_gate.configured() and redis_admission_gate.foreground_present()):
        raise PrefixEvaluationForegroundDeferred()


@contextmanager
def diagnostic_request():
    if not storage.postgres_is_configured():
        raise diagnostic_error('unsupported_backend', 'Prefix evaluation requires the authoritative PostgreSQL product.')
    deadline = monotonic() + COMPUTATION_SECONDS
    try:
        # HTTP middleware also classifies these endpoints, including callers without headers.
        with activity_gate.background_request():
            check_available(deadline)
            yield deadline
            check_available(deadline)
    except PrefixEvaluationError as error:
        status = 413 if error.code == 'limit_exceeded' else 422 if error.code == 'invalid_selection' else 409
        raise diagnostic_error(error.code, str(error), status) from error
    except (psycopg.OperationalError, PoolTimeout, redis.RedisError) as error:
        raise diagnostic_error('evaluation_busy', 'The diagnostic read service is temporarily unavailable. Retry after study work settles.', 503) from error


def load_snapshot(identifier, deadline, *, capture=None, reads=None):
    """Capture optional additional read rows in the same structural transaction.

    The callback may read bounded raw rows only; decoding and calculation remain
    outside the transaction. Existing evaluator callers retain their contract.
    """
    check_available(deadline)
    # No chess, JSON interpretation or application fingerprinting inside this transaction.
    with storage.structural_snapshot(reads) as database:
        if storage.repertoire(database, identifier) is None:
            raise diagnostic_error('repertoire_not_found', 'Repertoire not found.', 404)
        publication = storage.graph_publication(database, identifier)
        if publication is None or publication['state'] != 'ready' or (publication['task_generation'] is not None and
                (publication['task_generation'] > publication['generation'] or publication['task_state'] != 'complete')):
            raise diagnostic_error('graph_not_ready', 'Wait for the repertoire graph to finish publishing, then refresh the source.')
        sizes = storage.source_sizes(database, identifier)
        if sizes['line_count'] > MAX_SOURCE_LINES or sizes['source_bytes'] > MAX_SOURCE_BYTES:
            raise PrefixEvaluationError('limit_exceeded', 'Repertoire exceeds the bounded source evaluation limits.')
        source_rows = storage.source_rows(database, identifier, MAX_SOURCE_LINES + 1)
        step_rows = storage.graph_steps(database, identifier, publication['generation'], MAX_GRAPH_STEPS + 1)
        if len(step_rows) > MAX_GRAPH_STEPS:
            raise PrefixEvaluationError('limit_exceeded', 'Repertoire exceeds the bounded graph evaluation limit.')
        # Production currently applies a global split map. Fence that same map conservatively.
        split_rows = storage.split_rows(database, MAX_GRAPH_STEPS + 1)
        if len(split_rows) > MAX_GRAPH_STEPS:
            raise PrefixEvaluationError('limit_exceeded', 'Saved split map exceeds the bounded evaluation limit.')
        card_rows = storage.published_cards(database, identifier, publication['generation'])
        additional_rows = capture(database) if capture is not None else None
    check_available(deadline)
    try:
        lines = []
        for row in source_rows:
            lines.append(SourceLine(row['id'], row['name'], row['start_fen'], tuple(json.loads(row['moves_json'])),
                                    row['trained_color'], row['learner_decision_count']))
            check_available(deadline)
        splits = tuple(SplitOverride(row['source_card_id'], row['source_revision'], row['shortened_card_id'],
            row['shortened_start_fen'], tuple(json.loads(row['shortened_moves_json'])), row['shortened_revision'],
            row['continuation_card_id'], row['continuation_start_fen'], tuple(json.loads(row['continuation_moves_json'])),
            row['continuation_revision']) for row in split_rows)
        steps = tuple(GraphStep(row['repertoire_id'], row['line_id'], row['decision_index'], row['segment_kind'],
            row['first_decision_index'], row['last_decision_index'], tuple(json.loads(row['decision_fen_keys_json'])),
            row['card_id'], row['parent_card_id'], row['decision_fen_key'], row['starting_fen'],
            tuple(json.loads(row['moves_json'])), row['trained_color']) for row in step_rows)
        cards = tuple(PublishedCard(row['id'], row['start_fen'], tuple(json.loads(row['moves_json'])),
            row['trained_color'], row['revision'], row['archived'], bool(row['linked'])) for row in card_rows)
        snapshot = EvaluationSnapshot(identifier, publication['generation'], tuple(lines), splits, steps, cards)
        validate_source(snapshot)
        return snapshot if capture is None else (snapshot, additional_rows)
    except (ValueError, TypeError, KeyError) as error:
        if isinstance(error, PrefixEvaluationError): raise
        raise PrefixEvaluationError('unsupported_source', 'Saved source or graph data is malformed. Repair it before evaluating.') from error


def verify_snapshot(snapshot, deadline):
    current = load_snapshot(snapshot.repertoire_id, deadline)
    if snapshot_identity(current) != snapshot_identity(snapshot):
        raise diagnostic_error('stale_snapshot', 'The structural source changed. Refresh the source and compare again.')


@router.get('/api/repertoires/{identifier}/prefix-evaluation/source', response_model=SourceResponse)
def prefix_evaluation_source(identifier: str):
    with diagnostic_request() as deadline:
        snapshot = load_snapshot(identifier, deadline)
        token = snapshot_identity(snapshot)
        distribution = Counter(line.saved_depth for line in snapshot.lines)
        result = {'version': EVALUATION_VERSION, 'preview_only': True, 'estimate_basis': ESTIMATE_BASIS,
                  'repertoire_id': identifier, 'graph_generation': snapshot.graph_generation, 'snapshot_id': token,
                  'lines': [line.__dict__ for line in snapshot.lines],
                  'current_depth_distribution': [{'depth': depth, 'line_count': count} for depth, count in sorted(distribution.items())]}
        verify_snapshot(snapshot, deadline)
        return result


@router.post('/api/repertoires/{identifier}/prefix-evaluation/evaluate', response_model=PrefixEvaluationResponse)
def prefix_evaluation_compare(identifier: str, request: PrefixEvaluationRequest):
    with diagnostic_request() as deadline:
        snapshot = load_snapshot(identifier, deadline)
        if request.snapshot_id != snapshot_identity(snapshot):
            raise diagnostic_error('stale_snapshot', 'This structural snapshot is stale. Refresh the source and compare again.')
        calculation = iter_prefix_evaluation(snapshot, request.selected_line_ids, request.candidate_depths)
        try:
            while True:
                check_available(deadline)
                try: next(calculation)
                except StopIteration as completed:
                    result = completed.value
                    break
        finally:
            calculation.close()
        verify_snapshot(snapshot, deadline)
        return result
