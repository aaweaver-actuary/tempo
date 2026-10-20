"""Feedback decisions over typed facts, without a driver, database or SQL fake."""
import ast
import importlib
from pathlib import Path
import sys

import pytest

# These application modules have no runtime dependency beyond the standard library.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import real_game_feedback as policy
from app.services.real_game_feedback_ports import MissCard


class Store:
    def __init__(self, *, state='learning', introduced_at='2026-10-20', outstanding=True,
                 buried=False, accepted=None, event_card='card'):
        self.facts = MissCard('card', state, introduced_at) if outstanding else None
        self.buried, self.accepted, self.card = buried, accepted, event_card
        self.calls = []

    def lock_card(self, card_id): self.calls.append(('lock_card', card_id))
    def outstanding_card(self, card_id):
        self.calls.append(('outstanding', card_id)); return self.facts
    def is_buried(self, card_id, day):
        self.calls.append(('buried', card_id, day)); return self.buried
    def request_introduction(self, day): self.calls.append(('introduction', day))
    def lock_queue_day(self, day): self.calls.append(('lock_day', day))
    def enqueue_review(self, card_id, day, reason): self.calls.append(('enqueue', card_id, day, reason))
    def prioritize_queued_misses(self, day): self.calls.append(('prioritize', day))
    def event_card(self, event_id):
        self.calls.append(('event', event_id)); return self.card
    def accepted_miss_priority(self, card_id):
        self.calls.append(('accepted', card_id)); return self.accepted
    def replace_miss_priority(self, card_id, previous, replacement):
        self.calls.append(('replace', card_id, previous, replacement))


@pytest.mark.parametrize('state', ['new', 'locked'])
def test_unintroduced_miss_requests_admission_without_queue_or_schedule_mutation(state):
    store = Store(state=state, introduced_at=None)
    assert policy.promote_real_game_card(store, 'card', 'day')
    assert store.calls == [('lock_card', 'card'), ('outstanding', 'card'),
                           ('buried', 'card', 'day'), ('introduction', 'day')]


@pytest.mark.parametrize('state', ['learning', 'mature'])
def test_studied_miss_uses_card_before_day_lock_and_canonical_queue_effects(state):
    store = Store(state=state)
    assert policy.promote_real_game_card(store, 'card', 'day')
    assert store.calls == [('lock_card', 'card'), ('outstanding', 'card'), ('buried', 'card', 'day'),
                           ('lock_day', 'day'), ('enqueue', 'card', 'day', policy.MISS_REASON), ('prioritize', 'day')]


@pytest.mark.parametrize('options', [dict(outstanding=False), dict(buried=True),
                                    dict(state='new'), dict(state='locked'), dict(state='suspended')])
def test_non_actionable_facts_never_request_queue_mutation(options):
    store = Store(**options)
    assert not policy.promote_real_game_card(store, 'card', 'day')
    assert not any(call[0] in {'introduction', 'lock_day', 'enqueue', 'prioritize'} for call in store.calls)


@pytest.mark.parametrize('card_id', [None, ''])
def test_missing_event_card_does_not_lock_or_mutate(card_id):
    store = Store(event_card=card_id)
    assert not policy.prioritize_real_game_miss(store, 'event', 'day')
    assert store.calls == [('event', 'event')]


def test_event_promotion_keeps_supplied_day_instead_of_reading_a_clock():
    store = Store()
    assert policy.prioritize_real_game_miss(store, 'event', 'original-day')
    assert ('enqueue', 'card', 'original-day', policy.MISS_REASON) in store.calls


@pytest.mark.parametrize('accepted', [None, 'accepted bridge obligation'])
def test_satisfied_legacy_miss_retains_semantic_accepted_priority(accepted):
    store = Store(outstanding=False, accepted=accepted)
    policy.clear_satisfied_miss_priority(store, 'card')
    assert store.calls == [('outstanding', 'card'), ('accepted', 'card'),
                           ('replace', 'card', policy.MISS_REASON, accepted)]


def test_unsatisfied_miss_never_clears_priority():
    store = Store()
    policy.clear_satisfied_miss_priority(store, 'card')
    assert store.calls == [('outstanding', 'card')]


@pytest.mark.parametrize('module', ['real_game_feedback', 'real_game_feedback_ports'])
def test_isolated_feedback_application_has_no_sql_driver_or_concrete_adapter_dependency(module):
    source = Path(importlib.import_module('app.services.' + module).__file__).read_text()
    tree = ast.parse(source)
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    allowed = {'collections.abc', 'dataclasses', 'datetime', 'typing', 'real_game_feedback_ports'}
    for node in imports:
        names = [node.module] if isinstance(node, ast.ImportFrom) else [item.name for item in node.names]
        assert all(name in allowed for name in names), names
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in {'execute', 'execute_native', 'cursor', 'fetchone', 'fetchall',
                                     'commit', 'rollback', 'connect', 'create_function'}
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not any(token in node.value.upper() for token in ('SELECT ', 'INSERT INTO ', 'UPDATE ', 'DELETE FROM '))
