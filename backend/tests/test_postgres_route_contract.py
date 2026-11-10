"""Keep every HTTP route visible in the PostgreSQL cutover audit."""

from __future__ import annotations

import json
import asyncio
from pathlib import Path
import re

from starlette.requests import Request
from starlette.responses import Response

from app import main
from tempo_legacy_database.adapters import postgres_store


CONTRACT_PATH = (
    Path(__file__).resolve().parents[2] / "docs/postgres-route-contract.json"
)
TREATMENTS = {
    "reader",
    "read_only_post",
    "foreground_command",
    "background_callback",
    "ephemeral_signal",
    "queue_issuance",
}


def test_postgres_route_contract_admits_durable_game_review_mutations(monkeypatch):
    declared_routes = json.loads(CONTRACT_PATH.read_text())
    blocked_routes = {
        (row["method"], row["path"], row["handler"])
        for row in declared_routes
        if row["state"] == "blocked"
    }

    # The reader API now dispatches these writes through the durable command
    # gateway. No provider execution or scheduling write happens in the reader.
    assert blocked_routes == set()
    game_review_writes = {
        ("POST", "/api/product/game-review/sync", "sync"),
        ("POST", "/api/product/game-review/refresh", "refresh"),
        ("POST", "/api/product/game-review/feedback/retry", "retry_feedback"),
        ("POST", "/api/product/game-review/configuration", "configure"),
    }
    assert {
        (row["method"], row["path"], row["handler"])
        for row in declared_routes
        if row["method"] == "POST"
        and row["path"].startswith("/api/product/game-review/")
        and row["treatment"] == "foreground_command"
        and row["state"] == "staged"
    } == game_review_writes
    monkeypatch.setattr(postgres_store, "configured", lambda: True)

    async def probe_routes():
        for method, path, handler in sorted(game_review_writes):
            request = Request(
                {
                    "type": "http",
                    "method": method,
                    "path": path,
                    "headers": [],
                    "query_string": b"",
                    "scheme": "http",
                    "server": ("test", 80),
                }
            )
            downstream_called = False

            async def downstream(_request):
                nonlocal downstream_called
                downstream_called = True
                return Response(status_code=204)

            response = await main.prioritize_foreground_requests(request, downstream)
            route = (method, path, handler)
            assert response.status_code == 204, route
            assert downstream_called, route

    asyncio.run(probe_routes())


def test_staged_postgres_mutations_pass_the_runtime_write_guard(monkeypatch):
    monkeypatch.setattr(postgres_store, "configured", lambda: True)

    async def probe_routes():
        blocked = []
        for route in json.loads(CONTRACT_PATH.read_text()):
            if route["method"] == "GET" or route["state"] != "staged":
                continue
            path = re.sub(r"\{[^}]+\}", "sample", route["path"])
            request = Request(
                {
                    "type": "http",
                    "method": route["method"],
                    "path": path,
                    "headers": [],
                    "query_string": b"",
                    "scheme": "http",
                    "server": ("test", 80),
                }
            )

            async def downstream(_request):
                return Response(status_code=204)

            response = await main.prioritize_foreground_requests(request, downstream)
            if response.status_code != 204:
                blocked.append((route["method"], route["path"], response.status_code))
        return blocked

    assert asyncio.run(probe_routes()) == []


def test_retired_routes_do_not_dispatch_or_match_game_wildcard(monkeypatch):
    from fastapi.testclient import TestClient
    from app import command_dispatch

    def unexpected_dispatch(*args, **kwargs):
        raise AssertionError("retired request dispatched a command")

    monkeypatch.setattr(command_dispatch, "dispatch_command", unexpected_dispatch)
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    client = TestClient(main.app)
    for method, path in (
        ("GET", "/api/games/example/analysis"),
        ("GET", "/api/games/next-opponent-profile"),
        ("POST", "/api/endgames/probe"),
        ("POST", "/api/game-evidence/legacy/deliveries"),
        ("POST", "/api/game-evidence/legacy/cards/example/queue"),
        ("GET", "/api/game-evidence/legacy/cards/example"),
        ("POST", "/api/product/game-history/start"),
        ("POST", "/api/repertoire-coverage/maia/claim"),
    ):
        response = client.request(method, path)
        assert response.status_code in (404, 405), (method, path, response.status_code)


def test_recovery_task_routes_scope_status_and_preserve_retry_identity(monkeypatch):
    from contextlib import contextmanager
    from fastapi.testclient import TestClient
    from app import command_dispatch

    tasks = [
        {
            "id": "repair",
            "kind": "integrity_repair",
            "state": "failed",
            "age_seconds": 0,
        },
        {"id": "queue", "kind": "daily_queue", "state": "queued", "age_seconds": 3},
        {"id": "scan", "kind": "integrity_scan", "state": "failed", "age_seconds": 0},
        {"id": "retired", "kind": "game_analysis", "state": "failed", "age_seconds": 0},
    ]

    @contextmanager
    def reader():
        yield object()

    monkeypatch.setattr(main, "read_connection", reader)
    monkeypatch.setattr(main, "list_tasks", lambda: tasks)
    monkeypatch.setattr(
        main.system_storage, "recent_queue_projections", lambda database: []
    )
    monkeypatch.setattr(
        main,
        "annotate_queue_projection_workers",
        lambda projections, tasks, day: projections,
    )
    monkeypatch.setattr(
        main.durable_task_storage,
        "read_task",
        lambda database, values: next(
            (task for task in tasks if task["id"] == values[0]), None
        ),
    )
    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    calls = []
    monkeypatch.setattr(
        command_dispatch,
        "dispatch_command",
        lambda name, payload, **kwargs: (
            calls.append((name, payload, kwargs))
            or {"operation_id": kwargs["idempotency_key"]}
        ),
    )
    client = TestClient(main.app)
    status = client.get("/api/system/tasks")
    assert status.status_code == 200
    assert [task["id"] for task in status.json()["tasks"]] == [
        "repair",
        "queue",
        "scan",
    ]
    assert status.json()["counts"]["failed"] == 2
    assert (
        client.post(
            "/api/system/tasks/retired/retry", headers={"Idempotency-Key": "retired-op"}
        ).status_code
        == 404
    )
    assert calls == []
    for _ in range(2):
        response = client.post(
            "/api/system/tasks/repair/retry",
            headers={"Idempotency-Key": "original-retry"},
        )
        assert (
            response.status_code == 200
            and response.json()["operation_id"] == "original-retry"
        )
    assert (
        calls
        == [
            (
                "activity.task.retry",
                {"task_id": "repair"},
                {"idempotency_key": "original-retry"},
            )
        ]
        * 2
    )
    response = client.post(
        "/api/system/tasks/scan/retry", headers={"Idempotency-Key": "scan-retry"}
    )
    assert response.status_code == 200
    assert calls[-1] == (
        "activity.task.retry",
        {"task_id": "scan"},
        {"idempotency_key": "scan-retry"},
    )
