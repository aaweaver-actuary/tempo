"""Keep every HTTP route visible in the PostgreSQL cutover audit."""

from __future__ import annotations

import json
import asyncio
from pathlib import Path
import re

from starlette.requests import Request
from starlette.responses import Response

from app import main, postgres_store
from app.main import app


CONTRACT_PATH = Path(__file__).resolve().parents[2] / "docs/postgres-route-contract.json"
TREATMENTS = {
    "reader", "read_only_post", "foreground_command", "background_callback",
    "ephemeral_signal",
}


def test_postgres_route_contract_matches_registered_endpoints():
    declared_routes = json.loads(CONTRACT_PATH.read_text())
    actual_routes = {
        (method, route.path, route.name)
        for route in app.routes if route.path.startswith("/api")
        for method in route.methods - {"HEAD", "OPTIONS"}
    }
    declared_keys = {
        (row["method"], row["path"], row["handler"])
        for row in declared_routes
    }

    assert len(declared_routes) == len(declared_keys)
    assert declared_keys == actual_routes
    assert all(row["treatment"] in TREATMENTS for row in declared_routes)
    assert all(row["state"] in {"staged", "blocked"} for row in declared_routes)
    assert all(row["treatment"] == "reader" for row in declared_routes
               if row["method"] == "GET")
    assert all(row["treatment"] != "reader" for row in declared_routes
               if row["method"] != "GET")


def test_postgres_route_contract_tracks_remaining_blocked_product_routes():
    declared_routes = json.loads(CONTRACT_PATH.read_text())
    blocked_handlers = {row["handler"] for row in declared_routes
                        if row["state"] == "blocked"}

    assert blocked_handlers == set()


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
