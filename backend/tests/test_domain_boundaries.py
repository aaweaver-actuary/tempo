"""B02 regular-suite certification of existing domain modules (#200)."""
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from domain_boundary import BoundaryViolation, dependency_graph, validate_exceptions


REPOSITORY = Path(__file__).resolve().parents[2]
FIXTURES = REPOSITORY / "tests/fixtures/domain-boundary"
RULES = json.loads((FIXTURES / "rules.json").read_text())


def synthetic_modules(tmp_path, sources):
    metadata = deepcopy(RULES)
    metadata["modules"] = list(sources)
    metadata["exceptions"] = []
    metadata["stdlib"] += ["importlib", "builtins"]
    for module, source in {"app": "", "app.services": "", **sources}.items():
        path = tmp_path.joinpath(*module.split("."))
        path = path / "__init__.py" if module in {"app", "app.services"} else path.with_suffix(".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    return metadata


@pytest.mark.parametrize("source,expected", [
    ("import psycopg", "psycopg"),
    ("from . import database", "app.services.database"),
    ("def hidden():\n    from app import main", "app.main"),
    ("if False:\n    import redis", "redis"),
    ("from .bridge import value", "psycopg"),
    ("__import__('celery')", "celery"),
    ("import importlib as loader\nloader.import_module('fastapi')", "fastapi"),
    ("from importlib import import_module as load\nload('.database', 'app.services')", "app.services.database"),
    ("loader = __import__\nloader('redis')", "redis"),
    ("loaders = [__import__]\nloaders[0]('redis')", "unreviewed import callable"),
    ("def load(name):\n    return __import__(name)", "unresolved dynamic import"),
])
def test_domain_import_rules_reject_transitive_database_and_entrypoint_dependencies(tmp_path, source, expected):
    sources = {"app.services.root": "from .leaf import value", "app.services.leaf": source,
               "app.services.bridge": "from .deep import value", "app.services.deep": "import psycopg"}
    metadata = synthetic_modules(tmp_path, sources)
    with pytest.raises(BoundaryViolation, match=expected) as failure:
        dependency_graph(tmp_path, metadata)
    assert "app.services.root -> app.services.leaf" in str(failure.value)


def test_domain_import_rules_include_package_initializers_and_reject_new_capabilities(tmp_path):
    metadata = synthetic_modules(tmp_path, {"app.services.root": "value = 1"})
    (tmp_path / "app/__init__.py").write_text("import sqlite3")
    with pytest.raises(BoundaryViolation, match="app.*sqlite3"):
        dependency_graph(tmp_path, metadata)


@pytest.mark.parametrize("source", [
    "open('data.json')", "def compute(store):\n    store.execute('SELECT 1')",
    "from datetime import datetime as clock\nclock.now()", "from fsrs import Optimizer",
    "import pathlib", "eval('1')", "__import__('unreviewed')", "from fsrs import *",
    "import fsrs\nfsrs.Optimizer()", "import chess\nchess.engine.SimpleEngine.popen_uci('engine')",
])
def test_domain_import_rules_reject_unreviewed_io_clocks_and_library_capabilities(tmp_path, source):
    metadata = synthetic_modules(tmp_path, {"app.services.root": source})
    with pytest.raises(BoundaryViolation):
        dependency_graph(tmp_path, metadata)


def test_domain_certified_dependency_graph_matches_reviewed_allowlist():
    actual = dependency_graph(REPOSITORY / "backend", RULES)
    assert actual == json.loads((FIXTURES / "dependency-graph.json").read_text())


SMOKE = """
import importlib, json, sys
sys.path[:0] = [sys.argv[1], sys.argv[1] + '/tests']
from domain_boundary_runtime import no_service_runtime
rules = json.loads(sys.argv[3])
before = set(sys.modules)
with no_service_runtime(certified_modules=rules['modules']):
    importlib.import_module(sys.argv[2])
    imported_packages = sorted({name.split('.')[0] for name, module in sys.modules.items()
                                if name not in before and 'site-packages' in str(getattr(module, '__file__', ''))})
    allowed_packages = {package for review in rules['third_party'].values()
                        for package in review['runtime_packages']}
    assert set(imported_packages) <= allowed_packages, imported_packages
    print(json.dumps({'module': sys.argv[2], 'runtime_packages': imported_packages}), file=sys.stderr)
    from domain_boundary_fixtures import fixed_contract
    from app.services.opening_ranking_evaluation import canonical_json
    result = fixed_contract()
    from app.services.opening_card_valuation import replay_opening_card_valuation
    assert replay_opening_card_valuation(result['valuation']['snapshot']) == {
        key: value for key, value in result['valuation'].items() if key != 'snapshot'}
    print(canonical_json(result))
assert not any(name.split('.')[0] in {'sqlite3','psycopg','psycopg_pool','redis','celery','fastapi','httpx'}
               for name in set(sys.modules) - before)
"""


def fresh_process(source, *arguments, seed="1"):
    return subprocess.run([sys.executable, "-I", "-B", "-c", source,
                           str(REPOSITORY / "backend"), *arguments],
                          env={"PYTHONHASHSEED": seed, "PYTHONDONTWRITEBYTECODE": "1"},
                          capture_output=True, text=True, timeout=20)


@pytest.mark.parametrize("module", RULES["modules"])
def test_domain_import_smoke_requires_no_database_or_network_initialization(module):
    completed = fresh_process(SMOKE, module, json.dumps(RULES))
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout == (FIXTURES / "fixed-as-of.json").read_text()


@pytest.mark.parametrize("operation", [
    "__import__('sqlite3')", "__import__('psycopg')", "__import__('app.main')",
    "socket.create_connection(('127.0.0.1', 1))", "socket.socket()",
    "subprocess.run([sys.executable, '-c', 'pass'])", "open('/tmp/b02-forbidden-write', 'w')",
    "open('/etc/hosts')",
    "os.open('/tmp/b02-forbidden.py', os.O_WRONLY | os.O_CREAT)",
])
def test_domain_runtime_firewall_rejects_deliberate_initialization(operation):
    source = """
import os, socket, subprocess, sys
sys.path[:0] = [sys.argv[1], sys.argv[1] + '/tests']
from domain_boundary_runtime import no_service_runtime
with no_service_runtime():
    try:
        exec(sys.argv[2])
    except AssertionError as error:
        print(str(error))
    else:
        raise AssertionError('Firewall failed to reject deliberate initialization')
"""
    completed = fresh_process(source, operation)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Forbidden" in completed.stdout


def test_valuation_readiness_snapshot_contract_is_identical_at_fixed_as_of():
    for seed in ("1", "77"):
        completed = fresh_process(SMOKE, "app.services.opening_card_valuation", json.dumps(RULES), seed=seed)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert completed.stdout == (FIXTURES / "fixed-as-of.json").read_text()
    manifest = json.loads((FIXTURES / "immutable-fixtures.json").read_text())
    for filename, digest in manifest["sha256"].items():
        assert hashlib.sha256((REPOSITORY / filename).read_bytes()).hexdigest() == digest, filename


@pytest.mark.parametrize("mutation", ["owner", "reason", "symbol", "regression", "missing_regression", "wildcard", "expired", "extended", "unused", "duplicate"])
def test_domain_boundary_exception_is_explicit_owned_and_bounded(mutation):
    metadata = deepcopy(RULES)
    observed = {tuple(item[field] for field in ("module", "symbol", "operation")) for item in metadata["exceptions"]}
    validate_exceptions(metadata, observed, today=date(2026, 10, 12))
    exception = metadata["exceptions"][0]
    if mutation in {"owner", "reason", "symbol", "regression"}:
        exception[mutation] = ""
    elif mutation == "missing_regression":
        exception["regression"] = "test_nonexistent_guard"
    elif mutation == "wildcard":
        exception["symbol"] = "*"
    elif mutation == "expired":
        exception["review_by"] = "2026-10-11"
    elif mutation == "extended":
        exception["review_by"] = "2026-11-13"
    elif mutation == "unused":
        observed.clear()
    else:
        metadata["exceptions"].append(deepcopy(exception))
    with pytest.raises(BoundaryViolation):
        validate_exceptions(metadata, observed, today=date(2026, 10, 12))


@pytest.mark.parametrize("replacement", ["datetime.now(None)", "datetime.now(); datetime.now()"])
def test_domain_exception_cannot_expand_to_changed_or_multiple_calls(tmp_path, replacement):
    module = "app.services.root"
    source = "from datetime import datetime\ndef schedule_review():\n    return datetime.now()"
    metadata = synthetic_modules(tmp_path, {module: source})
    exception = deepcopy(RULES["exceptions"][0])
    exception["module"] = module
    import ast
    call = next(node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call))
    exception["expression_sha256"] = hashlib.sha256(ast.unparse(call).encode()).hexdigest()
    metadata["exceptions"] = [exception]
    dependency_graph(tmp_path, metadata)
    (tmp_path / "app/services/root.py").write_text(source.replace("datetime.now()", replacement))
    with pytest.raises(BoundaryViolation, match="exception"):
        dependency_graph(tmp_path, metadata)


def test_domain_scheduler_clock_compatibility_is_bounded(monkeypatch):
    from datetime import datetime, timezone
    import fsrs.card
    from app.services import scheduler
    from domain_boundary_fixtures import fixed_contract

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 1, 12, tzinfo=timezone.utc)

    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 1)

    monkeypatch.setattr(scheduler, "datetime", FixedDatetime)
    monkeypatch.setattr(scheduler, "date", FixedDate)
    monkeypatch.setattr(fsrs.card, "datetime", FixedDatetime)
    sleeps = []
    monkeypatch.setattr(fsrs.card.time, "sleep", sleeps.append)
    legacy = scheduler.schedule_review("correct")
    explicit = scheduler.schedule_review("correct", reviewed_at=FixedDatetime.now(), review_day=FixedDate.today())
    assert legacy == explicit
    assert json.loads(legacy.fsrs_card_json)["card_id"] == 1790856000000
    assert sleeps == [.001, .001]
    sleeps.clear()
    fixed_contract()
    assert sleeps == []  # Persisted identity bypasses the FSRS construction clock/wait.


@pytest.mark.parametrize("fen", [
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 17 42",
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1",
    "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1",
    "4k3/8/8/3p4/8/8/8/4K3 w - d6 0 1",
    "invalid", "8/8/8/8/8/8/8/8 x - - 0 1",
])
def test_domain_probability_position_identity_preserves_legacy_contract(fen):
    from app.services.canonical_prefix import position_key as legacy
    from app.opening_move_probability_contracts import position_key
    try:
        expected = legacy(fen)
    except ValueError:
        with pytest.raises(ValueError):
            position_key(fen)
    else:
        assert position_key(fen) == expected
