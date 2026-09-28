"""Keep every HTTP route visible in the PostgreSQL cutover audit."""

from __future__ import annotations

import json
from pathlib import Path

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

    assert blocked_handlers == {
        "accept_discovery_continuation", "repair_one_stockfish_timeout",
        "repair_one_legacy_network_identity", "save_game_analysis",
        "claim_defensive_threat_analysis", "submit_defensive_threat_analysis",
        "fail_defensive_threat_analysis", "release_defensive_threat_analysis",
        "retry_defensive_threat_analysis",
    }
