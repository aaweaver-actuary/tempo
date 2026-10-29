"""Mounted optional receipt reads use a reader profile and the actual Study owner."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import httpx
import pytest

from test_product_game_review_receipts import scenario, forbidden


def test_mounted_game_review_reads_current_owner_without_a_writer_and_contains_failure(monkeypatch):
    case = scenario()
    # Use the root's actual workspace namespace throughout the synthetic source,
    # delivery and immutable effect; do not alter it inside the runtime binding.
    def in_legacy_workspace(value):
        return type(value).model_validate_json(value.model_dump_json().replace('"workspace"', '"legacy"'))
    case.source = in_legacy_workspace(case.source)
    case.command = in_legacy_workspace(case.command)
    case.store.source = case.source
    case.store.saved_deliveries = tuple(in_legacy_workspace(item) for item in case.store.saved_deliveries)
    monkeypatch.setenv("TEMPO_DATABASE_READ_URL", "postgresql://synthetic-reader/fixture")
    monkeypatch.delenv("TEMPO_DATABASE_WRITE_URL", raising=False)
    monkeypatch.delenv("TEMPO_REDIS_URL", raising=False)
    from app.product_composition import game_review as root
    from app import main

    calls, broken = [], [False]
    command = case.command
    row = dict(identity_json=command.key.model_dump_json(), source_revision=case.receipt.source_revision,
        received_at=datetime(2026, 10, 26, tzinfo=timezone.utc), status="pending",
        payload_json=json.dumps(dict(kind="encounter", command=command.model_dump(mode="json"),
            owner=dict(disposition="first_introduction", admission_status="pending", deferred_reason=None,
                       observation_recorded=True))), payload_hash="synthetic", available_by=None,
        card_id=command.decision.presentation_id, card_revision=1, reason="study_priority_pending",
        applied_at=None, review_id=None, settled_at=None, settlement_review_id=None,
        hold_json=None, conflict_json=None)

    class ReadConnections:
        def __init__(self, dsn, *, read_only):
            assert dsn == "postgresql://synthetic-reader/fixture" and read_only is True
            self.read_only = read_only
            calls.append("reader-profile")
        @contextmanager
        def __call__(self):
            calls.append("reader-section")
            def execute(sql, parameters=()):
                assert sql == "SELECT * FROM study_game_feedback WHERE effect_key=%s"
                from app.services.activity_gate import activity_gate
                assert activity_gate.in_background and not activity_gate.foreground_requests_active
                calls.append("owner-status-select")
                if broken[0]:
                    raise RuntimeError("synthetic optional receipt read failure")
                return SimpleNamespace(fetchone=lambda: row)
            yield SimpleNamespace(execute=execute)
        writer = staticmethod(forbidden)

    monkeypatch.setattr(root, "DedicatedGamesConnections", ReadConnections)
    monkeypatch.setattr(root, "PostgresReviewStore", lambda connections: case.store)
    monkeypatch.setattr(root, "CurrentStudyFeedback", forbidden)
    # The real application middleware remains in the request path. Only its
    # external admission transport is absent, as in the existing HTTP fixtures.
    monkeypatch.setattr(main.postgres_store, "configured", lambda: False)
    from inspect import getclosurevars
    route = next(route for route in main.app.routes
                 if route.path == "/api/product/game-review/games/{provider}/{game_id}")
    operations = getclosurevars(route.endpoint).nonlocals["operations"]
    # Restore the lazy root's original cache at fixture teardown as well as the
    # patched connection/store factories, so this case cannot leak its fixture.
    monkeypatch.setattr(operations, "_services", {})

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(main.app), base_url="http://fixture") as client:
            path = "/api/product/game-review/games/lichess/" + case.source.normalized.game_id
            response = await client.get(path)
            assert response.status_code == 200, response.text
            receipt, = response.json()["receipts"]
            assert receipt["disposition"] == "first_introduction"
            assert receipt["admission_status"] == "pending" and receipt["available_by"] is None
            assert receipt["observation_recorded"] is True
            assert receipt["source_digest"] == command.source.source_digest
            broken[0] = True
            failed = await client.get(path)
            assert failed.status_code == 503
            assert failed.json() == {"detail": "game_review_read_unavailable"}
            # The existing read-side root must not acquire a command credential.
            rejected = await client.post("/api/product/game-review/sync", json={"provider":"lichess", "operation_id":"synthetic-sync"})
            assert rejected.status_code == 503
            assert rejected.json() == {"detail": "games_writer_profile_required"}
    asyncio.run(request())
    assert calls.count("reader-profile") == 1
    assert calls.count("owner-status-select") == 2
    from app.services.activity_gate import activity_gate
    assert not activity_gate.foreground_requests_active and not activity_gate.in_background
