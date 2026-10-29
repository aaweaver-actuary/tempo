"""Bounded-reader relocation and preserved queue/presentation query boundaries."""
import ast
from pathlib import Path
import re

import pytest

from app.services import queue_application
from app.services.legacy_game_evidence_scope import BRIDGE_MISS_REASON
from tempo_legacy_database.adapters.queue_refresh import reads


def test_queue_refresh_and_capacity_keep_all_sql_and_driver_mechanics_in_root_database():
    base = Path(__file__).resolve().parents[1] / 'app/services'
    for filename in ('postgres_queue_refresh.py', 'opening_capacity_admission.py'):
        tree = ast.parse((base / filename).read_text())
        assert not [node for node in ast.walk(tree)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {'execute', 'execute_native', 'executemany', 'cursor', 'configured'}], filename
        assert not [node for node in ast.walk(tree)
                    if isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and re.match(r'^(SELECT|INSERT|UPDATE|DELETE|WITH|FROM)\b', node.value)], filename
    tree = ast.parse((base / 'queue_application.py').read_text())
    presentation = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == 'presentation_rows')
    assert not [node for node in ast.walk(presentation)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in {'execute', 'execute_native', 'executemany', 'cursor', 'configured'}]


@pytest.mark.parametrize('frozen_active', [False, True])
def test_study_query_preparation_captures_route_identity_before_read_callbacks(monkeypatch, frozen_active):
    statements = []
    read_last, read_page = reads.study_opening_queries(frozen_active)
    monkeypatch.setattr(reads, '_STUDY_OPENING_ROUTES', 'poisoned-later-route')
    monkeypatch.setattr(reads, '_STUDY_OPENING_ORDER', 'poisoned-later-order')

    def bounded(statement, parameters=(), *, native=False):
        assert native
        statements.append((statement, parameters))
        return []

    monkeypatch.setattr(reads, '_bounded_read', bounded)
    assert read_last() == []
    size = 8 if frozen_active else 6
    after, upper = tuple(range(size)), tuple(range(10, 10 + size))
    assert read_page(after, upper, 16) == []
    assert len(statements) == 2
    assert all('poisoned-later' not in statement for statement, _ in statements)
    assert statements[1][1] == (*after, *upper, 16)
    assert statements[1][0].count('%s') == 2 * size + 1


def test_empty_presentation_keeps_single_read_without_review_or_eligibility_queries(monkeypatch):
    statements = []
    monkeypatch.setattr(reads.postgres_store, 'configured', lambda: False)

    def read(statement, parameters):
        statements.append((statement, parameters))
        return []

    assert queue_application.presentation_rows(None, '2026-10-21', read=read,
                                              after_entry_id=7, limit=16) == []
    assert len(statements) == 1
    assert statements[0][1] == ('2026-10-21', 7, 16)


def _priority_presentation_row(number, reason, **values):
    return dict(id=number, card_id=f"card-{number}", position=number, content_type="opening",
        admission_kind=values.get('admission_kind', 'new'), gameplay_priority_reason=reason,
        cycle=values.get('cycle', 0), attempt_state="clean", attempt_failed=False,
        review_completed=False, revision=1, issued=False, presented=False, taught=False)


def _presentation_reader(rows, *, owed=(), reviewed=(), eligible=(), failure=None):
    calls = []
    def read(statement, parameters=()):
        assert not re.match(r'\s*(INSERT|UPDATE|DELETE)\b', statement, re.IGNORECASE)
        calls.append((statement, parameters))
        if 'FROM study_game_feedback' in statement:
            if failure is not None:
                raise failure
            return [(identity,) for identity in owed if identity in parameters]
        if statement.startswith('SELECT q.id,q.card_id'):
            return rows
        if statement.startswith('SELECT DISTINCT card_id FROM reviews'):
            return [(identity,) for identity in reviewed]
        return [(identity,) for identity in eligible]
    return read, calls


def test_game_priority_bounded_presentation_keeps_owed_labels_without_connection(monkeypatch):
    monkeypatch.setattr(reads.postgres_store, 'configured', lambda: False)
    rows = [_priority_presentation_row(1, 'product_game_lapse'),
            _priority_presentation_row(2, 'product_game_encounter', cycle=1),
            _priority_presentation_row(3, None, admission_kind='explicit')]
    reader, calls = _presentation_reader(rows, owed=('card-1', 'card-2'),
        reviewed=('card-1',), eligible=(1, 2))
    result = queue_application.presentation_rows(None, '2026-10-28', read=reader,
        after_entry_id=0, limit=16)
    assert [row['id'] for row in result] == [1, 2, 3]
    assert [row['card_id'] for row in result] == ['card-1', 'card-2', 'card-3']
    assert [row['gameplay_priority_reason'] for row in result] == ['product_game_lapse', 'product_game_encounter', None]
    assert [row['admission_kind'] for row in result] == ['review', 'review', 'explicit']
    assert [row['eligible'] for row in result] == [True, True, False]
    assert [row['position'] for row in result] == [1, 2, 3]
    assert result[2] == {**rows[2], 'eligible': False}
    owed_calls = [(sql, ids) for sql, ids in calls if 'FROM study_game_feedback' in sql]
    assert len(owed_calls) == 1 and owed_calls[0][1] == ('card-1', 'card-2')
    assert '?' in owed_calls[0][0] and all(identity not in owed_calls[0][0] for identity in ('card-1', 'card-2'))


def test_game_priority_bounded_presentation_clears_only_unowed_labels(monkeypatch):
    monkeypatch.setattr(reads.postgres_store, 'configured', lambda: False)
    rows = [_priority_presentation_row(number,
        'product_game_lapse' if number % 2 else 'product_game_encounter') for number in range(1, 16)]
    for number, row in enumerate(rows):
        row['card_id'] = f'card-{number % 4 + 1}'
    rows.append(_priority_presentation_row(16, 'unrelated-priority'))
    reader, calls = _presentation_reader(rows, owed=('card-1', 'card-2'), eligible=range(1, 17))
    result = queue_application.presentation_rows(None, '2026-10-28', read=reader, limit=16)
    assert [row['id'] for row in result] == list(range(1, 17))
    for original, projected in zip(rows[:-1], result[:-1]):
        assert projected['gameplay_priority_reason'] == (original['gameplay_priority_reason']
            if original['card_id'] in {'card-1', 'card-2'} else None)
    assert result[-1]['gameplay_priority_reason'] == 'unrelated-priority'
    owed_calls = [(sql, ids) for sql, ids in calls if 'FROM study_game_feedback' in sql]
    assert len(owed_calls) == 1 and owed_calls[0][1] == ('card-1', 'card-2', 'card-3', 'card-4')
    assert "status IN ('pending','applied')" in owed_calls[0][0]


def test_game_priority_native_presentation_uses_borrowed_connection():
    from tempo_legacy_database.adapters.study.game_feedback import owed_feedback_card_ids, has_owed_feedback
    calls = []
    statuses = {'pending-card': 'pending', 'applied-card': 'applied',
                'settled-card': 'settled', 'controlled-card': 'held'}
    class Borrowed:
        def execute_native(self, statement, parameters):
            calls.append((statement, parameters))
            assert '%s::text[]' in statement and '?' not in statement
            assert "status IN ('pending','applied')" in statement
            matched = [(identity,) for identity in parameters[0] if statuses.get(identity) in {'pending', 'applied'}]
            class Cursor:
                def fetchall(self):
                    return matched
            return Cursor()
        def close(self):
            pytest.fail('The helper must not close a borrowed connection')
        def commit(self):
            pytest.fail('The helper must not commit a borrowed connection')
    borrowed = Borrowed()
    ids = ('pending-card', 'applied-card', 'pending-card', 'settled-card', 'controlled-card', 'absent-card')
    assert owed_feedback_card_ids(borrowed, ids) == {'pending-card', 'applied-card'}
    assert len(calls) == 1 and calls[0][1] == (list(dict.fromkeys(ids)),)
    assert has_owed_feedback(borrowed, 'pending-card') and not has_owed_feedback(borrowed, 'settled-card')
    def forbidden(*args):
        pytest.fail('An empty batch must not perform a read')
    assert owed_feedback_card_ids(None, (), read=forbidden) == set()
    assert owed_feedback_card_ids(borrowed, ()) == set()
    with pytest.raises(RuntimeError, match='borrowed connection or bounded reader'):
        owed_feedback_card_ids(None, ('pending-card',))
    reader, callback_calls = _presentation_reader([], owed=('pending-card',))
    assert owed_feedback_card_ids(object(), ('pending-card',), read=reader) == {'pending-card'}
    assert len(callback_calls) == 1


def test_game_priority_lookup_failure_propagates_without_fabricating_settlement(monkeypatch):
    from psycopg import OperationalError
    monkeypatch.setattr(reads.postgres_store, 'configured', lambda: False)
    rows = [_priority_presentation_row(1, 'product_game_lapse'),
            _priority_presentation_row(2, 'product_game_encounter')]
    failure = OperationalError('owed-feedback-read-unavailable')
    reader, calls = _presentation_reader(rows, failure=failure)
    with pytest.raises(OperationalError, match='owed-feedback-read-unavailable') as error:
        queue_application.presentation_rows(None, '2026-10-28', read=reader, limit=16)
    assert error.value is failure
    assert [row['gameplay_priority_reason'] for row in rows] == ['product_game_lapse', 'product_game_encounter']
    assert sum('FROM study_game_feedback' in sql for sql, _ in calls) == 1


@pytest.mark.parametrize('reason', [None, BRIDGE_MISS_REASON])
def test_nonpriority_presentation_does_not_read_owed_feedback(monkeypatch, reason):
    monkeypatch.setattr(reads.postgres_store, 'configured', lambda: False)
    rows = [_priority_presentation_row(1, reason)]
    reader, calls = _presentation_reader(rows, eligible=(1,))
    result = queue_application.presentation_rows(None, '2026-10-28', read=reader, limit=16)
    assert result[0]['gameplay_priority_reason'] == reason and result[0]['eligible']
    assert not any('FROM study_game_feedback' in sql for sql, _ in calls)
