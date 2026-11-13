"""Retained prefix split routes use the assembled API and durable dispatch."""
from contextlib import asynccontextmanager, contextmanager

import chess
import pytest
from fastapi.testclient import TestClient

from app import command_dispatch, main, prefix_split_commands, prefix_split_routes
from app.command_gateway import CommandConflict
from app.contracts.command_identity import request_digest
from app.services import prefix_split


@pytest.fixture
def api(monkeypatch):
    @asynccontextmanager
    async def admitted():
        yield
    monkeypatch.setattr(main.activity_gate, "async_foreground", admitted)
    return TestClient(main.app)


def test_prefix_routes_mount_exactly_once():
    expected = {
        ("GET", "/api/cards/{identifier}/prefix-split"),
        ("POST", "/api/cards/{identifier}/prefix-split"),
        ("POST", "/api/cards/{identifier}/prefix-split/reject"),
    }
    routes = [(method, route.path) for route in main.app.routes
              for method in getattr(route, "methods", ()) if "prefix-split" in route.path]
    assert set(routes) == expected
    assert len(routes) == len(expected)


def test_prefix_preview_uses_read_owner_and_rejects_illegal_card(api, monkeypatch):
    reads = []
    @contextmanager
    def reader():
        reads.append("read")
        yield object()
    monkeypatch.setattr(prefix_split_routes, "read_connection", reader)
    card = {"content_type": "opening", "kind": "prefix", "trained_color": "white",
            "start_fen": chess.STARTING_FEN, "moves_json": '["e2e4", "e7e5", "g1f3"]',
            "revision": 7}
    monkeypatch.setattr(prefix_split.storage, "card_row", lambda db, key: card if key == "source" else None)
    monkeypatch.setattr(prefix_split_routes.storage, "published_usage", lambda db, key: {"line_count": 2, "repertoire_count": 1})
    response = api.get("/api/cards/source/prefix-split")
    assert response.status_code == 200
    assert response.json()["source_revision"] == 7
    assert response.json()["continuation"]["moves"] == ["e7e5", "g1f3"]
    assert response.json()["shared_line_count"] == 2
    assert reads == ["read"]
    card["moves_json"] = '["e2e5"]'
    assert api.get("/api/cards/source/prefix-split").status_code == 422
    assert api.get("/api/cards/missing/prefix-split").status_code == 404


@pytest.mark.parametrize("suffix,command", [("", "cards.prefix_split.accept"), ("/reject", "cards.prefix_split.reject")])
def test_prefix_decision_dispatch_preserves_operation_revision_and_receipt(api, monkeypatch, suffix, command):
    submissions, reads = [], []
    class Task:
        def get(self, **kwargs):
            return None
    def send_task(name, **kwargs):
        submissions.append((name, kwargs))
        return Task()
    monkeypatch.setattr(command_dispatch.celery_app, "send_task", send_task)
    state = {"state": "queued", "message": "waiting"}
    def read_operation(operation_id, **kwargs):
        reads.append((operation_id, kwargs))
        return state
    monkeypatch.setattr(command_dispatch, "read_operation", read_operation)
    path = "/api/cards/source/prefix-split" + suffix
    body = {"expected_revision": 7, "queue_entry_id": 42}
    headers = {"Idempotency-Key": "original-operation"}
    response = api.post(path, json=body, headers=headers)
    assert response.status_code == 202
    assert response.json() == {"operation_id": "original-operation", "state": "queued", "message": "waiting"}
    assert response.headers["location"] == "/api/operations/original-operation"
    payload = {"card_id": "source", "request": body}
    name, args = submissions[-1]
    assert name == "app.tasks.execute_foreground_command"
    assert args["args"] == ["original-operation", command, payload]
    assert args["task_id"] == "original-operation" and args["queue"] == "foreground"
    assert reads[-1] == ("original-operation", {"command_name": command, "request_hash": request_digest(command, payload), "background": False})
    state.clear()
    state.update({"state": "blocked", "message": "held"})
    response = api.post(path, json=body, headers=headers)
    assert response.status_code == 202 and response.json() == {
        "operation_id": "original-operation", "state": "blocked", "message": "held"
    }
    result = ({"source_card_id": "source", "source_revision": 7,
               "parent": {"card_id": "parent", "starting_fen": chess.STARTING_FEN, "moves": ["e2e4"], "tested_player_moves": 1},
               "continuation": {"card_id": "child", "starting_fen": chess.STARTING_FEN, "moves": ["e7e5", "g1f3"], "tested_player_moves": 1},
               "applied": True, "idempotent": False}
              if not suffix else {"rejected_after_review_id": 9})
    state.clear()
    state.update({"state": "complete", "response": result})
    response = api.post(path, json=body, headers=headers)
    assert response.status_code == 200
    assert all(response.json()[key] == value for key, value in result.items())
    state.clear()
    state.update({"state": "failed", "error": {"status_code": 409, "message": "stale revision"}})
    response = api.post(path, json=body, headers=headers)
    assert response.status_code == 409 and response.json()["detail"] == "stale revision"
    def conflict(*args, **kwargs):
        raise CommandConflict("operation payload differs")
    monkeypatch.setattr(command_dispatch, "read_operation", conflict)
    response = api.post(path, json={**body, "expected_revision": 8}, headers=headers)
    assert response.status_code == 409 and response.json()["detail"] == "operation payload differs"
    before = len(submissions)
    assert api.post(path, json={"expected_revision": 0}, headers=headers).status_code == 422
    assert len(submissions) == before


def test_prefix_accept_http_holds_issued_attempt_and_checks_replay_revision(api, monkeypatch):
    """Exercise the actual command and split policy, with persistence facts isolated."""
    from datetime import date
    from types import SimpleNamespace
    card = {"content_type": "opening", "kind": "prefix", "trained_color": "white",
            "start_fen": chess.STARTING_FEN, "moves_json": '["e2e4", "e7e5", "g1f3"]', "revision": 7}
    facts = {"prior": None, "active": True}
    monkeypatch.setattr(prefix_split_commands, "_lock_prefix_card", lambda *a: None)
    monkeypatch.setattr(prefix_split_commands.split_storage, "matches_queue_entry", lambda _db, _card, entry: entry == 42)
    monkeypatch.setattr(prefix_split_commands, "capture_study_calendar", lambda *a: SimpleNamespace(day=date(2026, 11, 12)))
    monkeypatch.setattr(prefix_split_commands, "lock_queue_date_for_position", lambda *a: None)
    monkeypatch.setattr(prefix_split_commands, "_shared_usage", lambda *a: {})
    monkeypatch.setattr(prefix_split.storage, "card_row", lambda *a: card)
    monkeypatch.setattr(prefix_split.storage, "prior_split", lambda *a: facts["prior"])
    monkeypatch.setattr(prefix_split.storage, "has_active_issued_attempt", lambda *a: facts["active"])
    writes = []
    monkeypatch.setattr(prefix_split, "capture_daily_queue_order", lambda *a: writes.append("mutation"))
    monkeypatch.setattr(command_dispatch, "dispatch_command", lambda _name, payload, **_kw:
                        prefix_split_commands.accept_prefix_split(object(), payload))
    path = "/api/cards/source/prefix-split"
    held = api.post(path, json={"expected_revision": 7, "queue_entry_id": 42}, headers={"Idempotency-Key": "original"})
    assert held.status_code == 409 and "issued attempt" in held.json()["detail"]
    wrong_entry = api.post(path, json={"expected_revision": 7, "queue_entry_id": 43}, headers={"Idempotency-Key": "wrong"})
    assert wrong_entry.status_code == 409 and "queue attempt" in wrong_entry.json()["detail"]
    assert writes == []
    facts["prior"] = {"source_revision": 7}
    replay = api.post(path, json={"expected_revision": 7, "queue_entry_id": 42}, headers={"Idempotency-Key": "original"})
    assert replay.status_code == 200 and replay.json()["idempotent"] is True
    stale = api.post(path, json={"expected_revision": 8}, headers={"Idempotency-Key": "other"})
    assert stale.status_code == 409 and "changed" in stale.json()["detail"]
    assert writes == []
