"""Explicit infrastructure identity/cold-import proof; no actual database use."""
import importlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def cold_import(module):
    """Fresh process with actual installed libraries and forbidden startup I/O."""
    script = r'''
import importlib, os, socket, subprocess, threading
import chess, httpx, pydantic, psycopg, psycopg_pool, sqlglot
def forbidden(*args, **kwargs): raise AssertionError("public import started infrastructure")
for operation in (lambda: forbidden(),):
    try: operation()
    except AssertionError: pass
    else: raise AssertionError("inactive negative control")
psycopg.connect = forbidden
psycopg_pool.ConnectionPool.__init__ = forbidden
socket.create_connection = forbidden
socket.socket.connect = forbidden
subprocess.Popen = forbidden
class Environment(dict):
    def get(self, key, default=None):
        if key.startswith(("TEMPO_DATABASE_", "TEMPO_REDIS_", "LICHESS_", "CHESSCOM_")):
            raise AssertionError("public import read operational configuration")
        return super().get(key, default)
    def __getitem__(self, key):
        if key.startswith(("TEMPO_DATABASE_", "TEMPO_REDIS_", "LICHESS_", "CHESSCOM_")):
            raise AssertionError("public import read operational configuration")
        return super().__getitem__(key)
os.environ = Environment(os.environ)
before = {thread.ident for thread in threading.enumerate()}
module = importlib.import_module(MODULE)
assert module.__all__ and len(module.__all__) == len(set(module.__all__))
assert all(not name.startswith("_") and hasattr(module, name) for name in module.__all__)
assert {thread.ident for thread in threading.enumerate()} == before
import sys
assert not {"app.main", "app.tasks", "app.celery_app", "tempo_legacy_database.connections"} & sys.modules.keys()
'''
    environment = {key: value for key, value in os.environ.items()
                   if key == "PATH" or key.startswith("PYTHON")}
    environment["PYTHONPATH"] = os.pathsep.join(str(ROOT / path) for path in (
        "backend", "database", "packages/chess-core/src", "packages/game-review-contracts/src",
        "contracts/game-evidence/src", "packages/evidence-math/src", "contracts/analysis-jobs/src"))
    result = subprocess.run([sys.executable, "-B", "-c", "MODULE=" + repr(module) + "\n" + script],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("module", ["tempo_legacy_database.interfaces", "app.operations.interfaces"])
def test_infrastructure_public_import_has_no_operational_startup(module):
    cold_import(module)


def test_infrastructure_exports_the_existing_driver_and_transaction_objects():
    public = importlib.import_module("tempo_legacy_database.interfaces")
    implementation = importlib.import_module("tempo_legacy_database.adapters.postgres_store")
    assert set(public.__all__) == {"PostgresConnection", "TempoRow", "close_pools", "configured",
        "connection", "diagnostic_read_connection", "tempo_row_factory"}
    for name in public.__all__:
        assert getattr(public, name) is getattr(implementation, name)
    row = public.TempoRow(("count", "scope"), (3, "synthetic"))
    assert row[0] == row["count"] == 3
    assert dict(row) == {"count": 3, "scope": "synthetic"}


def test_infrastructure_configuration_remains_explicit_and_does_not_open_a_pool(monkeypatch):
    from tempo_legacy_database import interfaces
    implementation = importlib.import_module("tempo_legacy_database.adapters.postgres_store")
    before = dict(implementation._pools)
    monkeypatch.delenv("TEMPO_DATABASE_WRITE_URL", raising=False)
    monkeypatch.delenv("TEMPO_DATABASE_READ_URL", raising=False)
    assert interfaces.configured() is False
    monkeypatch.setenv("TEMPO_DATABASE_READ_URL", "synthetic-never-connected")
    assert interfaces.configured() is True
    assert implementation._pools == before


def test_readiness_public_operation_is_the_pure_application():
    public = importlib.import_module("app.operations.interfaces")
    assert public.__all__ == ["ReadinessProbe", "ReadinessSnapshot", "ReadinessUnavailable", "evaluate_readiness"]
    assert public.evaluate_readiness is importlib.import_module("app.operations.postgres_readiness").postgres_health


def test_readiness_application_imports_without_runtime_or_frameworks():
    import ast
    tree = ast.parse((ROOT / "backend/app/operations/postgres_readiness.py").read_text())
    names = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any(name.startswith(("app.celery_app", "tempo_legacy_database", "fastapi")) for name in names)
    module = importlib.import_module("app.operations.postgres_readiness")
    assert module.postgres_health.__module__ == "app.operations.postgres_readiness"


@pytest.mark.parametrize("change,workers,message", [
    ({"schema_version": None}, frozenset(), "schema is not version"),
    ({"settings_present": False}, frozenset(), "settings are missing"),
    ({"queue_state": "failed", "queue_last_error": "synthetic failure"}, frozenset({"foreground"}), "background"),
    ({"queue_state": "failed", "queue_last_error": "synthetic failure"}, frozenset({"foreground", "background"}), "synthetic failure"),
    ({"queue_state": None}, frozenset({"foreground", "background"}), "queue cannot progress"),
])
def test_readiness_keeps_existing_failure_order_without_selecting_clients(change, workers, message):
    from dataclasses import replace
    from datetime import date
    from app.operations.interfaces import ReadinessSnapshot, ReadinessUnavailable
    from app.operations.interfaces import evaluate_readiness as postgres_health
    facts = ReadinessSnapshot(51, True, "ready", False, None, None, None)
    observed = []
    def queues():
        observed.append(True)
        return workers
    with pytest.raises(ReadinessUnavailable, match=message):
        postgres_health(read_snapshot=lambda: replace(facts, **change), worker_queues=queues,
            today=lambda: date(2026, 10, 27), expected_schema_version=51)
    assert bool(observed) == ("schema_version" not in change and "settings_present" not in change)


def test_readiness_accepts_ready_and_in_progress_facts_and_preserves_failure_boundaries():
    from dataclasses import FrozenInstanceError, replace
    from datetime import date
    from app.operations.interfaces import ReadinessSnapshot, ReadinessUnavailable
    from app.operations.interfaces import evaluate_readiness as postgres_health
    facts = ReadinessSnapshot(51, True, "ready", False, None, None, None)
    options = dict(worker_queues=lambda: frozenset({"foreground", "background"}),
                   today=lambda: date(2026, 10, 27), expected_schema_version=51)
    assert postgres_health(read_snapshot=lambda: facts, **options)["status"] == "ok"
    pending = replace(facts, queue_state="refreshing", queue_refresh_pending=True,
                      queue_task_state="leased", queue_task_payload_json='{"queue_date":"2026-10-27"}')
    assert postgres_health(read_snapshot=lambda: pending, **options)["status"] == "ok"
    with pytest.raises(FrozenInstanceError):
        facts.queue_state = "failed"
    def failed():
        raise RuntimeError("synthetic unavailable transport")
    with pytest.raises(ReadinessUnavailable, match="PostgreSQL reader unavailable"):
        postgres_health(read_snapshot=failed, **options)
    with pytest.raises(ReadinessUnavailable, match="Redis or Celery control is unavailable"):
        postgres_health(read_snapshot=lambda: facts, **(options | {"worker_queues": failed}))
