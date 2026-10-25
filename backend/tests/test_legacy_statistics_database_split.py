"""Retained game-feature persistence boundary."""
import ast
from pathlib import Path


def test_statistics_composition_delegates_all_sql_to_database_owner():
    path = Path(__file__).resolve().parents[1] / 'app/services/statistics.py'
    calls = [node for node in ast.walk(ast.parse(path.read_text()))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr in {'execute', 'execute_native', 'executemany', 'cursor'}]
    assert not calls
