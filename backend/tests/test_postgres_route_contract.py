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


def test_postgres_route_contract_matches_registered_endpoints(tmp_path):
    declared_routes = json.loads(CONTRACT_PATH.read_text())
    # Inspect the real PostgreSQL composition root independently of earlier
    # SQLite-mode imports in the regular suite. Importing does not run lifespan
    # or open a database; inert task-only URLs prevent borrowing live settings.
    environment = os.environ.copy()
    environment.pop("TEMPO_DATABASE_WRITE_URL", None)
    environment["TEMPO_DATABASE_READ_URL"] = (
        "postgresql://route_contract@127.0.0.1:1/unused"
    )
    environment["TEMPO_DB_PATH"] = str(tmp_path / "unused.sqlite")
    registered_routes = json.loads(
        subprocess.check_output(
            [
                sys.executable,
                "-c",
                """
import json
from app.main import app
print(json.dumps([
        (method, route.path, route.name)
        for route in app.routes if route.path.startswith("/api")
        for method in route.methods - {"HEAD", "OPTIONS"}
]))
""",
            ],
            env=environment,
            text=True,
            timeout=30,
        )
    )
    registered_routes = [tuple(route) for route in registered_routes]
    actual_routes = set(registered_routes)
    declared_keys = {
        (row["method"], row["path"], row["handler"]) for row in declared_routes
    }

    assert len(declared_routes) == len(declared_keys)
    assert len(registered_routes) == len(actual_routes)
    assert declared_keys == actual_routes
    assert all(row["treatment"] in TREATMENTS for row in declared_routes)
    assert all(row["state"] in {"staged", "blocked"} for row in declared_routes)
    assert {
        row["path"] for row in declared_routes if row["treatment"] == "queue_issuance"
    } == {
        "/api/queue/today",
        "/api/queue/prepared",
        "/api/queue/window",
    }
    assert all(
        row["treatment"]
        == (
            "queue_issuance"
            if row["path"]
            in {
                "/api/queue/today",
                "/api/queue/prepared",
                "/api/queue/window",
            }
            else "reader"
        )
        for row in declared_routes
        if row["method"] == "GET"
    )
    assert all(
        row["treatment"] != "reader"
        for row in declared_routes
        if row["method"] != "GET"
    )
    assert {
        (row["method"], row["path"])
        for row in declared_routes
        if row["path"].startswith("/api/opening-evidence/")
    } == {
        ("POST", "/api/opening-evidence/checkpoints"),
        ("GET", "/api/opening-evidence/attempts/{attempt_id}"),
    }
    assert {
        (row["method"], row["path"], row["handler"], row["treatment"], row["state"])
        for row in declared_routes
        if row["path"].startswith("/api/product/repertoires/")
    } == {
        (
            "GET",
            "/api/product/repertoires/{repertoire_id}/conflicts",
            "conflicts",
            "reader",
            "staged",
        ),
        (
            "POST",
            "/api/product/repertoires/{repertoire_id}/changes/preview",
            "preview",
            "foreground_command",
            "staged",
        ),
        (
            "POST",
            "/api/product/repertoires/{repertoire_id}/changes/decide",
            "decide",
            "foreground_command",
            "staged",
        ),
        (
            "GET",
            "/api/product/repertoires/{repertoire_id}/review",
            "review",
            "reader",
            "staged",
        ),
        (
            "GET",
            "/api/product/repertoires/{repertoire_id}/proposals",
            "pending",
            "reader",
            "staged",
        ),
        (
            "GET",
            "/api/product/repertoires/{repertoire_id}/evidence",
            "probability",
            "reader",
            "staged",
        ),
        (
            "POST",
            "/api/product/repertoires/{repertoire_id}/audit",
            "enforce",
            "foreground_command",
            "staged",
        ),
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
