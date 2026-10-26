"""Retained next-opponent publication storage boundary."""
import ast
from pathlib import Path




def test_remaining_derived_publication_composition_delegates_all_sql_to_database_owner():
    services = Path(__file__).resolve().parents[1] / 'app/services'
    for name in ('postgres_next_opponent',):
        calls = [node for node in ast.walk(ast.parse((services / f'{name}.py').read_text()))
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr in {'execute', 'execute_native', 'executemany', 'cursor'}]
        assert not calls, f'{name} still executes SQL directly'
