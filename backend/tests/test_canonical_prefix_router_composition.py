"""The HTTP owner receives its compatibility wake at app construction."""

from contextlib import contextmanager
import os
import subprocess
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import canonical_prefix_api as owner


def test_sqlite_router_apps_keep_distinct_wakes_and_order(monkeypatch):
    events = []

    @contextmanager
    def connection():
        events.append("begin")
        yield object()
        events.append("end")

    monkeypatch.setattr(owner, "connection", connection)
    monkeypatch.setattr(owner.storage, "postgres_is_configured", lambda: False)
    monkeypatch.setattr(owner, "parse_prefix", lambda text: ["e4"])
    monkeypatch.setattr(
        owner,
        "preview_command",
        lambda db, payload: events.append("preview") or {"ok": True},
    )
    monkeypatch.setattr(
        owner, "save_prefix", lambda db, payload: events.append("save") or {"ok": True}
    )
    from app.services import repertoire_coverage

    monkeypatch.setattr(
        repertoire_coverage,
        "enqueue_coverage_refresh",
        lambda identifier, automatic: events.append(
            ("coverage", identifier, automatic)
        ),
    )
    clients = []
    for label in ("first", "second"):
        app = FastAPI()
        app.include_router(
            owner.create_canonical_prefix_router(
                lambda label=label: events.append(label)
            )
        )
        clients.append(TestClient(app))
    assert (
        clients[0]
        .post("/api/repertoires/r/canonical-prefix/preview", json={"movetext": "e4"})
        .status_code
        == 200
    )
    assert events == ["begin", "preview", "end", "first"]
    events.clear()
    assert (
        clients[1]
        .put(
            "/api/repertoires/r/canonical-prefix",
            json={"preview_id": "p", "expected_revision": 0},
        )
        .status_code
        == 200
    )
    assert events == ["begin", "save", "end", ("coverage", "r", True), "second"]


def test_postgres_router_dispatches_without_wake(monkeypatch):
    from app import command_dispatch

    calls = []
    monkeypatch.setattr(owner.storage, "postgres_is_configured", lambda: True)
    monkeypatch.setattr(owner, "parse_prefix", lambda text: ["e4"])
    monkeypatch.setattr(
        command_dispatch,
        "dispatch_command",
        lambda name, payload, idempotency_key: (
            calls.append((name, payload, idempotency_key)) or {"ok": True}
        ),
    )
    app = FastAPI()
    app.include_router(
        owner.create_canonical_prefix_router(
            lambda: (_ for _ in ()).throw(AssertionError("wake"))
        )
    )
    client = TestClient(app)
    assert (
        client.post(
            "/api/repertoires/r/canonical-prefix/preview",
            json={"movetext": "e4"},
            headers={"Idempotency-Key": "preview"},
        ).status_code
        == 200
    )
    assert (
        client.put(
            "/api/repertoires/r/canonical-prefix",
            json={"preview_id": "p", "expected_revision": 0},
            headers={"Idempotency-Key": "save"},
        ).status_code
        == 200
    )
    assert [(name, key) for name, _, key in calls] == [
        ("repertoire.canonical_prefix.preview", "preview"),
        ("repertoire.canonical_prefix.save", "save"),
    ]


def test_fresh_factory_import_does_not_import_main():
    code = """
import importlib.abc, sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "app.main": raise AssertionError("root import")
sys.meta_path.insert(0, Guard())
from app.canonical_prefix_api import create_canonical_prefix_router
router = create_canonical_prefix_router(lambda: None)
assert [(route.path, route.methods) for route in router.routes] == [
    ("/api/repertoires/{identifier}/canonical-prefix", {"GET"}),
    ("/api/repertoires/{identifier}/canonical-prefix/preview", {"POST"}),
    ("/api/repertoires/{identifier}/canonical-prefix/preview/{preview_id}", {"GET"}),
    ("/api/repertoires/{identifier}/canonical-prefix", {"PUT"}),
]
assert "app.main" not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], env=os.environ.copy(), check=True)
