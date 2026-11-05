"""Games sync transport runs through supplied semantic capabilities."""

import os
import subprocess
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse

from app.games.sync.http import create_game_sync_router
from app.games.sync.interfaces import GameSyncEnqueueResponse, GameSyncStatusResponse


def test_sync_router_normalizes_dispatch_and_preserves_idempotency():
    calls = []
    app = FastAPI()
    app.include_router(
        create_game_sync_router(
            lambda request, key: (
                calls.append((request, key))
                or GameSyncEnqueueResponse(job_id="job", status="queued")
            ),
            lambda: GameSyncStatusResponse.model_validate(
                {
                    "providers": [],
                    "active_filters": {"days": 90, "speeds": [], "rated_only": True},
                    "active_job": None,
                }
            ),
        )
    )
    client = TestClient(app)
    response = client.post(
        "/api/games/sync",
        json={
            "lichess_username": " alice ",
            "chesscom_username": "  bob  ",
            "days": 12,
        },
        headers={"Idempotency-Key": "original-id"},
    )
    assert response.status_code == 202
    assert response.json() == {
        "imported": 0,
        "job_id": "job",
        "status": "queued",
        "providers": {},
    }
    assert calls[0][0].model_dump()["lichess_username"] == "alice"
    assert calls[0][0].model_dump()["chesscom_username"] == "bob"
    assert calls[0][0].days == 12 and calls[0][1] == "original-id"
    invalid = client.post(
        "/api/games/sync", json={"lichess_username": " ", "chesscom_username": "\t"}
    )
    assert invalid.status_code == 422 and len(calls) == 1
    assert invalid.json()["detail"] == "Set a Lichess or Chess.com username in Settings"
    pending_app = FastAPI()
    pending_app.include_router(
        create_game_sync_router(
            lambda *_: JSONResponse(
                status_code=202,
                content={"state": "pending"},
                headers={"Location": "/api/operations/op"},
            ),
            lambda: GameSyncStatusResponse.model_validate(
                {
                    "providers": [],
                    "active_filters": {"days": 90, "speeds": [], "rated_only": True},
                }
            ),
        )
    )
    pending = TestClient(pending_app).post(
        "/api/games/sync", json={"lichess_username": "alice"}
    )
    assert pending.status_code == 202 and pending.json() == {"state": "pending"}
    assert pending.headers["location"] == "/api/operations/op"


def test_sync_status_projects_results_filters_and_job_without_storage_fields():
    status = GameSyncStatusResponse.model_validate(
        {
            "providers": [
                {
                    "provider": "lichess",
                    "username": "alice",
                    "status": "idle",
                    "last_result": {
                        "provider": "lichess",
                        "username": "alice",
                        "status": "idle",
                        "fetched": 2,
                    },
                }
            ],
            "active_filters": {
                "days": 90,
                "speeds": ["blitz", "rapid", "classical"],
                "rated_only": True,
            },
            "active_job": {
                "id": "job",
                "status": "queued",
                "created_at": "then",
                "updated_at": "now",
            },
        }
    )
    app = FastAPI()
    app.include_router(
        create_game_sync_router(
            lambda *_: GameSyncEnqueueResponse(job_id="job", status="queued"),
            lambda: status,
        )
    )
    result = TestClient(app).get("/api/games/sync/status")
    assert result.status_code == 200
    value = result.json()
    assert value["providers"][0]["last_result"]["fetched"] == 2
    assert "last_result_json" not in value["providers"][0]
    assert value["active_filters"] == {
        "days": 90,
        "speeds": ["blitz", "rapid", "classical"],
        "rated_only": True,
    }
    assert (
        value["active_job"]["result"] is None
        and "result_json" not in value["active_job"]
    )


def test_sync_router_import_does_not_load_roots_or_providers():
    code = """\nimport builtins\noriginal = builtins.__import__\ndef guarded(name, *args, **kwargs):\n    if name in {'app.main', 'app.tasks', 'app.product_composition'} or name.startswith(('app.services', 'tempo_legacy_database', 'httpx')):\n        raise AssertionError(name)\n    return original(name, *args, **kwargs)\nbuiltins.__import__ = guarded\nfrom app.games.sync.http import create_game_sync_router
from app.games.sync.interfaces import GameSyncEnqueueResponse, GameSyncStatusResponse\nassert callable(create_game_sync_router)\n"""
    subprocess.run([sys.executable, "-c", code], env=os.environ.copy(), check=True)


def test_actual_binding_uses_supplied_status_and_sqlite_enqueue_order():
    from contextlib import contextmanager
    from app.product_composition.game_sync_http import (
        GameSyncHttpCapabilities,
        create_game_sync_http_binding,
    )

    events = []

    @contextmanager
    def connection(*, background):
        events.append(("open", background))
        yield "db"
        events.append(("close", background))

    capabilities = GameSyncHttpCapabilities(
        postgres_configured=lambda: False,
        dispatch=lambda *_: (_ for _ in ()).throw(
            AssertionError("unexpected dispatch")
        ),
        in_background=lambda: True,
        enqueue=lambda request, *, background: (
            events.append(("enqueue", request.lichess_username, background)) or "job"
        ),
        wake=lambda: events.append(("wake",)),
        connection=connection,
        job_status=lambda db, job_id: (
            events.append(("job", db, job_id)) or {"status": "queued"}
        ),
        provider_states=lambda db: [
            {
                "provider": "lichess",
                "username": "alice",
                "status": "idle",
                "last_result_json": None,
            }
        ],
        latest_job=lambda db: None,
        serialize_job=lambda row: row,
    )
    enqueue, read_status = create_game_sync_http_binding(capabilities)
    app = FastAPI()
    app.include_router(create_game_sync_router(enqueue, read_status))
    client = TestClient(app)
    assert (
        client.post("/api/games/sync", json={"lichess_username": " alice "}).status_code
        == 202
    )
    assert events == [
        ("enqueue", "alice", True),
        ("wake",),
        ("open", True),
        ("job", "db", "job"),
        ("close", True),
    ]
    response = client.get("/api/games/sync/status")
    assert response.status_code == 200
    assert response.json()["providers"][0]["last_result"] is None
    assert "last_result_json" not in response.json()["providers"][0]
