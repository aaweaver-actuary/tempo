"""Bounded-reader relocation and preserved queue/presentation query boundaries."""
import ast
from pathlib import Path
import re

import pytest

from app.services import queue_application
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
