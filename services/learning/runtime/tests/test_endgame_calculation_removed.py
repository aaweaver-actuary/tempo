"""Removal is explicit; saved definitions/receipts keep their existing owners."""

import ast
import asyncio
from contextlib import contextmanager, nullcontext
import json
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from tempo_learning_runtime import api
from tempo_learning_runtime.config import Configuration
from tempo_learning_runtime.database import current_database


class SavedDatabase:
    def __init__(self):
        self.receipts = {}
        self.reads = []

    @contextmanager
    def scope(self):
        token = current_database.set(self)
        try:
            yield self
        finally:
            current_database.reset(token)

    def transaction(self, *, read_only=False, **kwargs):
        assert read_only, "removed calculation must never open a writer"
        return nullcontext(self)

    def execute(self, statement, parameters=()):
        self.reads.append((statement, parameters))
        assert "operation_receipts" in statement or "endgame_templates" in statement
        self.row = self.receipts.get(parameters[0]) if parameters else None
        return self

    def fetchone(self):
        return self.row

    def fetchall(self):
        return [{"id": "saved-template", "card_id": "saved-card"}]

    def close(self):
        pass


class QuietActivity:
    def __init__(self, *args, **kwargs):
        pass

    def request(self):
        return nullcontext()

    def start(self):
        pass

    def close(self):
        pass


@pytest.fixture
def removal_app(monkeypatch):
    database = SavedDatabase()
    monkeypatch.setattr(api, "Database", lambda configuration: database)
    monkeypatch.setattr(api, "HostActivity", QuietActivity)
    app = api.create_app(Configuration("removed", "unused", "unused", "x" * 40, maintenance=False))
    return app, database


def test_removed_probe_and_new_attempt_have_no_database_or_provider_phase(removal_app):
    app, database = removal_app

    async def exercise():
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://testserver",
            headers={"Authorization": "Bearer " + "x" * 40},
        ) as client:
            probe = await client.post("/api/endgames/probe", json={"fen": "8/8/8/8/8/2k5/8/1K6 w - - 0 1"})
            assert probe.status_code == 410
            attempt = await client.post("/api/endgames/templates/saved-template/attempt")
            assert attempt.status_code == 410
            assert database.reads == []

    asyncio.run(exercise())


def test_only_existing_immutable_attempt_payload_can_replay(removal_app, monkeypatch):
    app, database = removal_app
    payload = {"template_id": "saved-template", "fen": "8/8/8/8/8/2k5/8/1K6 w - - 0 1",
               "target": "draw", "moves": []}
    database.receipts["saved-operation"] = {"command_name": "endgames.attempt.create", "payload_json": json.dumps(payload)}
    dispatches = []

    def dispatch(name, saved, operation_id):
        dispatches.append((name, saved, operation_id))
        return {"id": "saved-attempt", **saved}

    monkeypatch.setattr(api.gateway, "dispatch", dispatch)

    async def exercise():
        async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver",
            headers={"Authorization": "Bearer " + "x" * 40},
        ) as client:
            endpoint = "/api/endgames/templates/saved-template/attempt"
            missing = await client.post(endpoint, headers={"Idempotency-Key": "new-operation"})
            assert missing.status_code == 410
            for _ in range(2):
                replay = await client.post(endpoint, headers={"Idempotency-Key": "saved-operation"})
                assert replay.status_code == 200
                assert replay.json()["id"] == "saved-attempt"
            conflict = await client.post("/api/endgames/templates/other/attempt", headers={"Idempotency-Key": "saved-operation"})
            assert conflict.status_code == 409
            templates = await client.get("/api/endgames/templates")
            assert templates.json() == {"templates": [{"id": "saved-template", "card_id": "saved-card"}]}
        assert dispatches == [("endgames.attempt.create", payload, "saved-operation")] * 2
        assert all("tablebase_cache" not in statement for statement, _ in database.reads)

    asyncio.run(exercise())


def test_legacy_removed_triggers_raise_before_any_adapter_or_generator():
    """Execute the actual thin function bodies without importing the legacy host.

    The ordinary backend suite retains integration ownership. AST extraction
    avoids its unrelated server/broker/engine dependency closure for this cut.
    """
    root = Path(__file__).resolve().parents[4]
    source = ast.parse((root / "backend/app/main.py").read_text())
    names = {"probe_endgame", "create_endgame", "create_endgame_attempt"}
    functions = [node for node in source.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert {node.name for node in functions} == names
    for node in functions:
        node.decorator_list = []
    namespace = {"HTTPException": HTTPException, "Header": lambda **kwargs: None}
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *functions], type_ignores=[]))
    exec(compile(module, "legacy-endgame-trigger-bodies", "exec"), namespace)
    with pytest.raises(HTTPException) as probe:
        asyncio.run(namespace["probe_endgame"](object()))
    assert probe.value.status_code == 410
    with pytest.raises(HTTPException) as template:
        namespace["create_endgame"](object())
    assert template.value.status_code == 410
    with pytest.raises(HTTPException) as attempt:
        asyncio.run(namespace["create_endgame_attempt"]("template"))
    assert attempt.value.status_code == 410


def test_learning_calculation_has_no_worker_pool_or_provider_composition():
    import tempo_learning_runtime.database as database_module
    import inspect
    source = inspect.getsource(api)
    assert "EndgameIO" not in source
    assert "AsyncClient" not in source
    assert "generate_position" not in source
    assert not hasattr(database_module.Database, "close_endgames")
    assert not hasattr(database_module, "endgame_lane")


def test_legacy_saved_attempt_recovery_keeps_template_binding_without_generation(monkeypatch):
    from types import SimpleNamespace
    import sys
    from fastapi.responses import JSONResponse

    root = Path(__file__).resolve().parents[4]
    node = next(node for node in ast.parse((root / "backend/app/main.py").read_text()).body
                if isinstance(node, ast.AsyncFunctionDef) and node.name == "create_endgame_attempt")
    node.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node,
    ], type_ignores=[]))
    class CommandConflict(Exception):
        pass
    monkeypatch.setitem(sys.modules, "removed_legacy.command_gateway", SimpleNamespace(CommandConflict=CommandConflict))
    saved = {"payload_json": json.dumps({"template_id": "saved-template", "fen": "saved-fen"})}
    class Reader:
        def execute(self, statement, parameters):
            assert "payload_json FROM operation_receipts" in statement
            assert parameters == ("saved-operation",)
            return self
        def fetchone(self):
            return saved
    receipt = {"state": "complete", "response": {"id": "saved-attempt", "fen": "saved-fen"}}
    namespace = {"__package__": "removed_legacy", "HTTPException": HTTPException,
                 "JSONResponse": JSONResponse, "Header": lambda **kwargs: None, "json": json,
                 "postgres_store": SimpleNamespace(configured=lambda: True),
                 "read_operation": lambda *args, **kwargs: receipt,
                 "read_connection": lambda: nullcontext(Reader())}
    exec(compile(module, "legacy-saved-attempt-body", "exec"), namespace)
    recover = namespace["create_endgame_attempt"]
    assert asyncio.run(recover("saved-template", "saved-operation")) == receipt["response"]
    receipt["state"] = "pending"
    pending = asyncio.run(recover("saved-template", "saved-operation"))
    assert pending.status_code == 202
    with pytest.raises(HTTPException) as conflict:
        asyncio.run(recover("other-template", "saved-operation"))
    assert conflict.value.status_code == 409
