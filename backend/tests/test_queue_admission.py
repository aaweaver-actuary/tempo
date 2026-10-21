"""Semantic queue admission tests; no database, SQL fake, or driver needed."""
from dataclasses import asdict

import pytest

from app.services import queue_admission as application
from app.study.openings.capacity_frontier import CapacityRoute

DAY = '2026-10-20'


def candidate(card_id, repertoire_id='rep', **facts):
    return dict(id=card_id, repertoire_id=repertoire_id, moves_json='["e2e4"]',
                gameplay_priority_reason=None, priority_date=None, priority_score=0,
                completed_line_ids_json='[]', frontier_decisions_json='[]', **facts)


class Store:
    miss_reason = 'outstanding miss'

    def __init__(self, rows=(), *, counts=None, capacity=False, frozen=False):
        self.rows = list(rows)
        self.counts = counts or {}
        self.capacity = capacity
        self.frozen = frozen
        self.effects = []
        self.calls = []
        self.capacity_rows = []
        self.routes = ()
        self.current_allowance = 10
        self.route_valid = True
        self.token_valid = True
        self.misses = set()

    def frozen_active(self): return self.frozen
    def opening_candidate_page(self, day, after_card_id): return application.OpeningCandidatePage(tuple(self.rows), None)
    def annotate_openings(self, candidates): return candidates
    def introduction_count_page(self, day, after_repertoire_id, *, reviewed_only=False):
        self.calls.append(('counts', reviewed_only))
        return application.IntroductionCountPage(tuple(self.counts.items()), None)
    def capacity_enabled(self): return self.capacity
    def study_only(self): return False
    def opening_limits(self, default): return default if isinstance(default, dict) else {'rep': default}
    def capacity_routes(self, repertoire_id, parents):
        self.calls.append(('routes', repertoire_id, parents))
        return self.routes
    def capacity_candidates(self, day, identifiers):
        self.calls.append(('capacity', identifiers))
        return self.capacity_rows
    def frozen_proposal_valid(self, proposal):
        self.calls.append(('token', proposal['card_id']))
        return self.token_valid
    def capacity_route_valid(self, proposal, day):
        self.calls.append(('route', proposal['capacity_route']))
        return self.route_valid
    def introduced_count(self, day, repertoire_id):
        return self.counts.get(repertoire_id, 0)
    def current_limit(self, repertoire_id): return self.current_allowance
    def introduce_opening(self, day, proposal, position):
        self.effects.append((day, proposal, position))
        repertoire_id = proposal['repertoire_id']
        self.counts[repertoire_id] = self.counts.get(repertoire_id, 0) + 1
    def unseen_openings(self, day): return self.rows
    def has_outstanding_miss(self, card_id): return card_id in self.misses
    def retain_unseen(self, day, card_id): self.effects.append(('retain', card_id))
    def remove_unseen(self, entry_id, card_id): self.effects.append(('remove', entry_id, card_id))


def test_semantic_admission_deduplicates_shared_cards_and_charges_historical_consumption():
    store = Store([candidate('shared', 'first'), candidate('shared', 'second'), candidate('next', 'second')],
                  counts={'first': 1})
    assert application.admit_prioritized_opening_cards(store, DAY, {'first': 1, 'second': 1}, 41) == 42
    assert [(row[1]['card_id'], row[1]['repertoire_id'], row[2]) for row in store.effects] == [('next', 'second', 42)]


def test_outstanding_miss_bypasses_allowance_and_preserves_reason():
    row = candidate('miss')
    row['gameplay_priority_reason'] = Store.miss_reason
    store = Store([row], counts={'rep': 10})
    assert application.admit_prioritized_opening_cards(store, DAY, 0, -1) == 0
    assert store.effects[0][1]['reason'] == Store.miss_reason


def capacity_store():
    store = Store([candidate('parent')], capacity=True)
    store.routes = (('child', CapacityRoute('rep', 7, 'line', 1, 'parent', False)),)
    store.capacity_rows = [candidate('child')]
    return store


@pytest.mark.parametrize('failure', ['route', 'token', 'current_limit', 'requested_limit', 'missing_limit'])
def test_capacity_admission_rechecks_exact_witness_and_current_budget_before_effects(failure):
    store = capacity_store()
    plan = application.plan_opening_admissions(store, DAY, {'rep': 2})
    capacity = plan[-1]
    assert capacity[1]['capacity_route'] == asdict(store.routes[0][1])
    # Model the normal parent having been admitted before the capacity proposal.
    store.counts['rep'] = 1
    if failure == 'route': store.route_valid = False
    if failure == 'token': store.token_valid = False
    if failure == 'current_limit': store.current_allowance = 1
    if failure == 'missing_limit': store.current_allowance = None
    requested = 1 if failure == 'requested_limit' else 2
    original = application.plan_opening_admissions
    try:
        application.plan_opening_admissions = lambda *args: [capacity]
        assert application.admit_prioritized_opening_cards(store, DAY, {'rep': requested}, 9) == 9
    finally:
        application.plan_opening_admissions = original
    assert store.effects == []
    assert ('routes', 'rep', ('parent',)) in store.calls


def test_capacity_parent_precedes_child_and_no_recursive_capacity_parent_is_offered():
    store = capacity_store()
    assert application.admit_prioritized_opening_cards(store, DAY, 2, -1) == 1
    assert [row[1]['card_id'] for row in store.effects] == ['parent', 'child']
    assert ('routes', 'rep', ('parent',)) in store.calls
    assert ('capacity', ('child',)) in store.calls


def test_reconciliation_preserves_miss_exception_and_counts_reviewed_introductions():
    store = Store([{'id': 1, 'card_id': 'miss', 'repertoire_id': 'rep'},
                   {'id': 2, 'card_id': 'other', 'repertoire_id': 'rep'}], counts={'rep': 1})
    store.misses.add('miss')
    application.reconcile_unseen_queue(store, DAY, 1)
    assert store.effects == [('retain', 'miss'), ('remove', 2, 'other')]
    assert store.calls == [('counts', True)]


def test_frozen_reconciliation_keeps_existing_queue_without_reading_or_mutating_it():
    store = Store(frozen=True)
    application.reconcile_unseen_queue(store, DAY, 0)
    assert store.effects == store.calls == []


def test_semantic_candidate_cursor_advances_through_an_empty_eligible_page():
    class PagedStore(Store):
        def opening_candidate_page(self, day, after_card_id):
            self.calls.append(('candidate_page', after_card_id))
            return (application.OpeningCandidatePage((), 'rejected-page') if after_card_id == ''
                    else application.OpeningCandidatePage((candidate('eligible'),), None))
    store = PagedStore()
    assert application.admit_prioritized_opening_cards(store, DAY, 1, 0) == 1
    assert [row[1]['card_id'] for row in store.effects] == ['eligible']
    assert store.calls[:2] == [('candidate_page', ''), ('candidate_page', 'rejected-page')]
