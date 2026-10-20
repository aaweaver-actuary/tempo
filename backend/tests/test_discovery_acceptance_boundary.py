"""Keep Discovery acceptance policy free of SQL, drivers and service startup."""

import ast
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
POLICY = REPOSITORY / "backend/app/services/discovery_acceptance_application.py"
CONTRACT = POLICY.with_name("discovery_acceptance_contracts.py")
ALLOWED_IMPORTS = {"datetime", "hashlib", "typing", "dataclasses", "tempo_chess_core",
                   "discovery_acceptance_contracts"}


def violations(source):
    errors = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module]
            if any(name not in ALLOWED_IMPORTS for name in names):
                errors.append((node.lineno, "infrastructure import"))
        if isinstance(node, ast.Attribute) and node.attr in {
            "execute", "execute_native", "executemany", "raw", "cursor", "connect",
            "connection", "commit", "rollback", "import_module", "exec_module", "load_module",
        }:
            errors.append((node.lineno, "driver or dynamic loader"))
        if isinstance(node, ast.Name) and node.id in {"__import__", "eval", "exec"}:
            errors.append((node.lineno, "dynamic loader"))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.search(
            r"\b(SELECT\s+.+\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b",
            node.value, re.IGNORECASE | re.DOTALL,
        ):
            errors.append((node.lineno, "SQL statement"))
    return errors


@pytest.mark.parametrize("path", [POLICY, CONTRACT])
def test_discovery_acceptance_policy_has_no_sql_or_driver_imports(path):
    assert violations(path.read_text()) == []


@pytest.mark.parametrize("addition", [
    "import psycopg", "def hidden():\n    from app import database",
    "if False:\n    from .discovery_acceptance_store import PostgresDiscoveryAcceptanceStore",
    "def hidden(store):\n    store.execute('something')",
    "QUERY = 'SELECT state FROM background_tasks'",
    "def hidden():\n    __import__('psycopg')",
])
def test_discovery_acceptance_boundary_rejects_infrastructure_regressions(addition):
    assert violations(POLICY.read_text() + "\n" + addition)


def test_discovery_acceptance_store_exposes_semantic_capabilities_only():
    from app.services.discovery_acceptance_contracts import DiscoveryAcceptanceStore
    assert {name for name in DiscoveryAcceptanceStore.__dict__ if not name.startswith("_")} == {
        "has_matching_intent", "lock_matching_intent", "lock_opportunity_scope",
        "task_state", "requeue_intent", "persist_intent_and_task",
    }


def test_discovery_policy_suite_runs_without_backend_services():
    source = '''
import sys
from importlib.abc import MetaPathFinder
from pathlib import Path
import pytest
root = Path(sys.argv[1])
sys.path[:0] = [str(root / 'backend'), str(root / 'packages/chess-core/src')]
class RejectInfrastructure(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('app.') and fullname not in {
            'app.services', 'app.services.discovery_acceptance_application',
            'app.services.discovery_acceptance_contracts'}:
            raise AssertionError('Unexpected backend dependency: ' + fullname)
        if fullname.split('.')[0] in {'psycopg','psycopg_pool','redis','celery','fastapi','sqlite3'}:
            raise AssertionError('Infrastructure dependency: ' + fullname)
sys.meta_path.insert(0, RejectInfrastructure())
def reject_connections(event, arguments):
    if event in {'socket.connect', 'socket.bind', 'sqlite3.connect'}:
        raise AssertionError('Service initialization: ' + event)
sys.addaudithook(reject_connections)
raise SystemExit(pytest.main([str(root / 'backend/tests/test_postgres_discovery_acceptance.py'),
    '-q', '-p', 'no:cacheprovider', '--rootdir=' + str(root),
    '-k', 'not route and not command_composes and not preparation']))
'''
    result = subprocess.run([sys.executable, "-I", "-B", "-c", source, str(REPOSITORY)],
                            env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                                 "PYTHONDONTWRITEBYTECODE": "1"},
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "29 passed" in result.stdout
