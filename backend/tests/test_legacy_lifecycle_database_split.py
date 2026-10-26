"""Retained queue-provenance, rollover and metric persistence boundary."""
import ast
from pathlib import Path


def test_three_lifecycle_compositions_delegate_sql_and_ddl_to_database_owner():
    application = Path(__file__).resolve().parents[1] / 'app'
    for relative in ('queue_attempt_origins.py', 'tasks.py', 'services/background_metrics.py'):
        calls = [node for node in ast.walk(ast.parse((application / relative).read_text()))
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr in {'execute', 'execute_native', 'executemany', 'cursor'}]
        assert not calls, f'{relative} still executes database operations directly'
    rollover = ast.parse((application / 'tasks.py').read_text())
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == 'tempo_legacy_database.adapters.lifecycle.refresh_requests'
        and any(name.name == 'daily_queue_projection' for name in node.names)
        for node in ast.walk(rollover)
    ), 'The essential rollover projection must keep its existing database owner'
