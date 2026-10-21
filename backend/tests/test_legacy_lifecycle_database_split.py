"""Scoped queue-provenance, refresh-intent and metric persistence boundary."""
import ast
from pathlib import Path


def test_three_lifecycle_compositions_delegate_sql_and_ddl_to_database_owner():
    application = Path(__file__).resolve().parents[1] / 'app'
    for relative in ('queue_attempt_origins.py', 'services/refresh_requests.py',
                     'services/background_metrics.py'):
        calls = [node for node in ast.walk(ast.parse((application / relative).read_text()))
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr in {'execute', 'execute_native', 'executemany', 'cursor'}]
        assert not calls, f'{relative} still executes database operations directly'
