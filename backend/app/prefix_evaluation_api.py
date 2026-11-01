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
from app.contracts.prefix_evaluation import PrefixEvaluationRequest, PrefixEvaluationResponse, SourceResponse
from .services.activity_gate import activity_gate
from .services import redis_admission_gate
from app.contracts.opening_graph import GraphStep
from .services.prefix_evaluation import (
    EVALUATION_VERSION, ESTIMATE_BASIS, MAX_SOURCE_LINES, MAX_GRAPH_STEPS,
    EvaluationSnapshot, SourceLine, SplitOverride, PublishedCard, PrefixEvaluationError,
    iter_prefix_evaluation, snapshot_identity, validate_source,
)

from .contracts.prefix_errors import PrefixEvaluationForegroundDeferred
from .services.prefix_evaluation_source import (
    MAX_SOURCE_BYTES, COMPUTATION_SECONDS, diagnostic_error, check_available,
    diagnostic_request, load_snapshot, verify_snapshot,
)


class DiagnosticRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def diagnostic_handler(request):
            try: return await handler(request)
            except RequestValidationError as error:
                raise diagnostic_error('invalid_selection', 'Provide a snapshot, source line IDs, and integer depths from 1 to 20.', 422) from error
        return diagnostic_handler


router = APIRouter(route_class=DiagnosticRoute)


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
