"""Bounded reader-only transition planning, with no receipts or persisted plans."""
from datetime import date

from fastapi import APIRouter

from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.prefix_reads import transitions as storage
from . import prefix_evaluation_api as structural
from .prefix_transition_contracts import PrefixTransitionPlan, PrefixTransitionRequest
from .services.prefix_evaluation import PrefixEvaluationError, snapshot_identity
from .services.prefix_transition import (
    SnapshotTable, TransitionSnapshot, canonical_json, iter_transition_plan,
    transition_snapshot_identity, validate_shortening,
)

MAX_TRANSITION_ROWS = 40_000
MAX_TRANSITION_BYTES = 4 * 1024 * 1024
router = APIRouter(route_class=structural.DiagnosticRoute)


def discover_memberships(identifier, deadline):
    structural.check_available(deadline)
    with storage.membership_connection() as database:
        rows = storage.membership_rows(database, identifier, MAX_TRANSITION_ROWS + 1)
    if len(rows) > MAX_TRANSITION_ROWS:
        raise PrefixEvaluationError('limit_exceeded', 'Too many memberships for a bounded transition plan.')
    structural.check_available(deadline)
    return tuple(row['card_id'] for row in rows)


def load_transition_snapshot(identifier, lookup_card_ids, study_day, deadline, *, reads=None):
    """All transition inputs and structure share one read-only MVCC snapshot."""
    lookup_card_ids = tuple(sorted(set(lookup_card_ids)))
    def capture(database):
        return storage.capture_transition_tables(database, identifier, lookup_card_ids,
            MAX_TRANSITION_ROWS, MAX_TRANSITION_BYTES, PrefixEvaluationError)

    source, raw_tables = structural.load_snapshot(identifier, deadline, capture=capture, **({'reads': reads} if reads is not None else {}))
    tables = []
    for name, rows in raw_tables:
        tables.append(SnapshotTable(name, tuple(canonical_json(dict(row)) for row in rows)))
        structural.check_available(deadline)
    return TransitionSnapshot(source, study_day, tuple(tables), lookup_card_ids)


def _calculate(generator, deadline):
    try:
        while True:
            structural.check_available(deadline)
            try:
                next(generator)
            except StopIteration as completed:
                return completed.value
    finally:
        generator.close()


@router.post('/api/repertoires/{identifier}/prefix-transition/plan', response_model=PrefixTransitionPlan)
def prefix_transition_plan(identifier: str, request: PrefixTransitionRequest):
    with structural.diagnostic_request() as deadline:
        try:
            source = structural.load_snapshot(identifier, deadline)
            if request.snapshot_id != snapshot_identity(source):
                raise PrefixEvaluationError('stale_snapshot', 'This source snapshot is stale. Refresh it before planning.')
            validate_shortening(source, request.selected_line_ids, request.candidate_depths)
            evaluation = _calculate(structural.iter_prefix_evaluation(source, request.selected_line_ids, request.candidate_depths), deadline)
            lookup_ids = {step['card_id'] for phase in ('current', 'proposed')
                          for step in evaluation['whole_repertoire'][phase]['steps']}
            lookup_ids.update(discover_memberships(identifier, deadline))
            for split in source.prefix_overrides:
                lookup_ids.update((split.source_card_id, split.shortened_card_id, split.continuation_card_id))
            study_day = date.today().isoformat()
            snapshot = load_transition_snapshot(identifier, lookup_ids, study_day, deadline)
            if snapshot_identity(snapshot.source) != request.snapshot_id:
                raise PrefixEvaluationError('stale_snapshot', 'Source changed during planning. Refresh and retry.')
            plan = _calculate(iter_transition_plan(snapshot, request.selected_line_ids, request.candidate_depths, evaluation), deadline)
            refreshed = load_transition_snapshot(identifier, lookup_ids, study_day, deadline)
            if transition_snapshot_identity(refreshed) != plan.transition_snapshot_id:
                raise PrefixEvaluationError('stale_plan', 'Source, cards, history, memberships, or attempts changed during planning. Refresh and retry.')
            return plan
        except storage.BUSY_ERRORS as error:
            raise structural.diagnostic_error('evaluation_busy', 'The bounded transition read could not finish. Retry when study is idle.', 503) from error
        except storage.UNSUPPORTED_SOURCE_ERROR as error:
            raise structural.diagnostic_error('unsupported_source', 'Saved command context is malformed. Repair the retained context before planning.', 409) from error
