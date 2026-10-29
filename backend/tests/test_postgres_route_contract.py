"""Keep every HTTP route visible in the PostgreSQL cutover audit."""

from __future__ import annotations

import json
import asyncio
import os
from pathlib import Path
import re
import subprocess
import sys

from starlette.requests import Request
from starlette.responses import Response

from app import main
from tempo_legacy_database.adapters import postgres_store


CONTRACT_PATH = Path(__file__).resolve().parents[2] / "docs/postgres-route-contract.json"
TREATMENTS = {
    "reader", "read_only_post", "foreground_command", "background_callback",
    "ephemeral_signal", "queue_issuance",
}


def test_postgres_route_contract_matches_registered_endpoints(tmp_path):
    declared_routes = json.loads(CONTRACT_PATH.read_text())
    # Inspect the real PostgreSQL composition root independently of earlier
    # SQLite-mode imports in the regular suite. Importing does not run lifespan
    # or open a database; inert task-only URLs prevent borrowing live settings.
    environment = os.environ.copy()
    environment.pop("TEMPO_DATABASE_WRITE_URL", None)
    environment["TEMPO_DATABASE_READ_URL"] = "postgresql://route_contract@127.0.0.1:1/unused"
    environment["TEMPO_DB_PATH"] = str(tmp_path / "unused.sqlite")
    registered_routes = json.loads(subprocess.check_output([
        sys.executable, "-c", """
import json
from app.main import app
print(json.dumps([
        (method, route.path, route.name)
        for route in app.routes if route.path.startswith("/api")
        for method in route.methods - {"HEAD", "OPTIONS"}
]))
"""], env=environment, text=True, timeout=30))
    registered_routes = [tuple(route) for route in registered_routes]
    actual_routes = set(registered_routes)
    declared_keys = {
        (row["method"], row["path"], row["handler"])
        for row in declared_routes
    }

    assert len(declared_routes) == len(declared_keys)
    assert len(registered_routes) == len(actual_routes)
    assert declared_keys == actual_routes
    assert all(row["treatment"] in TREATMENTS for row in declared_routes)
    assert all(row["state"] in {"staged", "blocked"} for row in declared_routes)
    assert {row["path"] for row in declared_routes if row["treatment"] == "queue_issuance"} == {
        "/api/queue/today", "/api/queue/prepared", "/api/queue/window",
    }
    assert all(row["treatment"] == ("queue_issuance" if row["path"] in {
        "/api/queue/today", "/api/queue/prepared", "/api/queue/window",
    } else "reader") for row in declared_routes if row["method"] == "GET")
    assert all(row["treatment"] != "reader" for row in declared_routes
               if row["method"] != "GET")
    assert {(row["method"], row["path"], row["handler"], row["treatment"], row["state"])
            for row in declared_routes
            if row["path"].startswith("/api/product/repertoires/")} == {
        ("GET", "/api/product/repertoires/{repertoire_id}/conflicts", "conflicts", "reader", "staged"),
        ("POST", "/api/product/repertoires/{repertoire_id}/changes/preview", "preview", "foreground_command", "staged"),
        ("POST", "/api/product/repertoires/{repertoire_id}/changes/decide", "decide", "foreground_command", "staged"),
        ("GET", "/api/product/repertoires/{repertoire_id}/review", "review", "reader", "staged"),
        ("GET", "/api/product/repertoires/{repertoire_id}/proposals", "pending", "reader", "staged"),
        ("GET", "/api/product/repertoires/{repertoire_id}/evidence", "probability", "reader", "staged"),
        ("POST", "/api/product/repertoires/{repertoire_id}/audit", "enforce", "foreground_command", "staged"),
    }


def test_postgres_route_contract_tracks_remaining_blocked_product_routes(monkeypatch):
    declared_routes = json.loads(CONTRACT_PATH.read_text())
    blocked_routes = {(row["method"], row["path"], row["handler"])
                      for row in declared_routes if row["state"] == "blocked"}

    # These writes remain refused until their durable command wiring lands.
    assert blocked_routes == {
        ("POST", "/api/product/game-review/sync", "sync"),
        ("POST", "/api/product/game-review/feedback/retry", "retry_feedback"),
        ("POST", "/api/product/game-review/configuration", "configure"),
    }
    monkeypatch.setattr(postgres_store, "configured", lambda: True)

    async def probe_routes():
        for method, path, handler in sorted(blocked_routes):
            request = Request({
                "type": "http", "method": method, "path": path,
                "headers": [], "query_string": b"", "scheme": "http",
                "server": ("test", 80),
            })
            downstream_called = False

            async def downstream(_request):
                nonlocal downstream_called
                downstream_called = True
                return Response(status_code=204)

            response = await main.prioritize_foreground_requests(request, downstream)
            route = (method, path, handler)
            assert response.status_code == 503, route
            assert json.loads(response.body) == {
                "detail": "This write route is awaiting its Celery cutover; use the current local Docker service.",
            }, route
            assert not downstream_called, route

    asyncio.run(probe_routes())


def test_staged_postgres_mutations_pass_the_runtime_write_guard(monkeypatch):
    monkeypatch.setattr(postgres_store, "configured", lambda: True)

    async def probe_routes():
        blocked = []
        for route in json.loads(CONTRACT_PATH.read_text()):
            if route["method"] == "GET" or route["state"] != "staged":
                continue
            path = re.sub(r"\{[^}]+\}", "sample", route["path"])
            request = Request({
                "type": "http", "method": route["method"], "path": path,
                "headers": [], "query_string": b"", "scheme": "http",
                "server": ("test", 80),
            })

            async def downstream(_request):
                return Response(status_code=204)

            response = await main.prioritize_foreground_requests(request, downstream)
            if response.status_code != 204:
                blocked.append((route["method"], route["path"], response.status_code))
        return blocked

    assert asyncio.run(probe_routes()) == []
