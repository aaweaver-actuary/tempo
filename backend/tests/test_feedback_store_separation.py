"""Borrowed feedback composition retains choices outside fixed root operations."""
import ast
from pathlib import Path

import pytest

from app.services import real_game_feedback_store as composition
from app.services.real_game_feedback_ports import MissCard, MissEvidence


def test_priority_plan_retains_slot_order_and_writes_only_changed_positions(monkeypatch):
    from app.services import queue_application, queue_ordering
    database, events = object(), []
    entries = [dict(id='a', position=20), dict(id='b', position=10), dict(id='c', position=30)]
    monkeypatch.setattr(queue_application, 'presentation_rows',
                        lambda connection, day: events.append(('read', connection, day)) or entries)
    def order(rows):
        events.append(('order', tuple(row['id'] for row in rows)))
        return [rows[2], rows[1], rows[0]]
    monkeypatch.setattr(queue_ordering, '_priority_queue_order', order)
    monkeypatch.setattr(composition.storage, 'set_queue_position',
                        lambda *args: events.append(('write', *args)))
    composition.BorrowedFeedbackStore(database).prioritize_queued_misses('day')
    assert events == [('read', database, 'day'), ('order', ('b', 'a', 'c')),
                      ('write', database, 10, 'c'), ('write', database, 30, 'b')]


@pytest.mark.parametrize('native,current', [(False, False), (True, False), (True, True)])
def test_accepted_priority_acquires_current_authority_only_on_native_path(monkeypatch, native, current):
    from app.services import legacy_study_remediation, legacy_game_evidence_scope
    database, events = object(), []
    monkeypatch.setattr(composition.storage, 'is_postgres',
                        lambda value: events.append(('dialect', value)) or native)
    monkeypatch.setattr(legacy_study_remediation, 'has_current_miss',
                        lambda *args: events.append(('current', *args)) or current)
    result = composition.BorrowedFeedbackStore(database).accepted_miss_priority('card')
    assert result == (legacy_game_evidence_scope.BRIDGE_MISS_REASON if native and current else None)
    assert events == [('dialect', database)] + ([('current', database, 'card')] if native else [])


@pytest.mark.parametrize('method,rows,expected', [
    ('missed_event_batches', [[(7,), ('event',)], [('last',)]], [('7', 'event'), ('last',)]),
    ('repertoire_miss_batches', [[dict(id='event', card_id='card', game_id='game', played_at='instant')]],
     [(MissEvidence('event', 'card', 'game', 'instant'),)]),
])
def test_typed_batch_mapping_is_lazy_and_retains_storage_iteration(monkeypatch, method, rows, expected):
    database, events = object(), []
    def batches(connection, identity):
        events.append(('query', connection, identity))
        for batch in rows:
            events.append(('fetch', batch))
            yield batch
        events.append(('exhausted',))
    monkeypatch.setattr(composition.storage, method, batches)
    result = getattr(composition.BorrowedFeedbackStore(database), method)('scope')
    assert events == []
    assert next(result) == expected[0]
    assert events == [('query', database, 'scope'), ('fetch', rows[0])]
    assert list(result) == expected[1:]
    assert events[-1] == ('exhausted',)


def test_typed_card_projection_maps_facts_without_storage_rows_leaking(monkeypatch):
    database = object()
    monkeypatch.setattr(composition.storage, 'outstanding_cards',
                        lambda *_: [dict(id=7, state='learning', introduced_at='day')])
    assert composition.BorrowedFeedbackStore(database).outstanding_cards(('7',)) == (MissCard('7', 'learning', 'day'),)


def test_existing_introduction_and_review_capabilities_keep_borrowed_identity_and_options(monkeypatch):
    from app.services import queue_application, review_service
    database, events = object(), []
    monkeypatch.setattr(queue_application, 'request_queue_refresh_in_transaction',
                        lambda *args: events.append(('refresh', args)))
    monkeypatch.setattr(review_service, 'ensure_card_queued_after',
                        lambda *args, **kwargs: events.append(('review', args, kwargs)))
    store = composition.BorrowedFeedbackStore(database)
    store.request_introduction('day')
    store.enqueue_review('card', 'day', 'reason')
    assert events == [('refresh', (database, 'day')),
                      ('review', (database, 'card', 0), dict(priority_reason='reason', queue_date='day',
                                                          protect_presented=True, preserve_order=False))]


def test_feedback_composition_has_no_sql_driver_calls_and_fixed_store_has_no_app_callbacks():
    tree = ast.parse(Path(composition.__file__).read_text())
    forbidden = {'execute', 'execute_native', 'cursor', 'fetchone', 'fetchall', 'fetchmany',
                 'commit', 'rollback', 'connect', 'create_function'}
    assert not any(isinstance(node, ast.Attribute) and node.attr in forbidden for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Constant) and isinstance(node.value, str)
                   and any(word in node.value.upper() for word in ('SELECT ', 'INSERT INTO ', 'UPDATE ', 'DELETE FROM '))
                   for node in ast.walk(tree))
    for node in ast.walk(ast.parse(Path(composition.storage.__file__).read_text())):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or '').startswith('app')
        elif isinstance(node, ast.Import):
            assert all(not name.name.startswith('app') for name in node.names)
