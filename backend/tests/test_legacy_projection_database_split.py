"""Scoped projection ownership and bound tactical-filter checks."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from tempo_legacy_database.adapters.projections import tactical_opportunities


def test_three_projection_compositions_delegate_sql_to_database_owner():
    services = Path(__file__).resolve().parents[1] / 'app' / 'services'
    for name in ('repertoire_comparison', 'repertoire_statistics', 'tactical_opportunities'):
        calls = [node for node in ast.walk(ast.parse((services / f'{name}.py').read_text()))
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr in {'execute', 'execute_native', 'executemany', 'cursor'}]
        assert not calls, f'{name} still executes SQL directly'


@pytest.mark.parametrize('projection,statement_count', [
    (tactical_opportunities.sqlite_tactical_rows, 1),
    (tactical_opportunities.postgres_tactical_rows, 2),
])
def test_tactical_projection_binds_every_filter_in_existing_order(projection, statement_count):
    statements = []

    class Database:
        def execute(self, statement, parameters):
            statements.append((statement, list(parameters)))
            return SimpleNamespace(fetchall=lambda: [])

        execute_native = execute

    filters = {
        'from_date': '2026-01-01', 'to_date': '2026-12-31',
        'provider': "provider' OR 1=1 --", 'speed': 'rapid',
        'color': 'white', 'motif': 'pin', 'outcome': 'missed',
    }
    projection(Database(), filters)
    assert len(statements) == statement_count
    for statement, parameters in statements:
        assert parameters == list(filters.values())
        assert filters['provider'] not in statement
