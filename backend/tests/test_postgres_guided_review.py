"""Regressions for serialized PostgreSQL guided review commands."""

from __future__ import annotations

from contextlib import nullcontext
import json
from types import SimpleNamespace


def test_postgres_guided_review_routes_dispatch_foreground_receipts(monkeypatch):
    from fastapi.testclient import TestClient
    from app import command_dispatch, main

    dispatched = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                        dispatched.append((name, payload, idempotency_key)) or {"status": "active"})
    client = TestClient(main.app)
    started = client.post("/api/games/provider:one/guided-review",
                          headers={"Idempotency-Key": "start-one"})
    attempted = client.post("/api/guided-reviews/session-one/attempt",
                            json={"move_uci": "e2e4"},
                            headers={"Idempotency-Key": "attempt-one"})
    assert started.status_code == attempted.status_code == 200
    assert dispatched == [
        ("games.guided_review.start", {"game_id": "provider:one"}, "start-one"),
        ("games.guided_review.attempt",
         {"session_id": "session-one", "move_uci": "e2e4"}, "attempt-one"),
    ]


def test_postgres_guided_review_start_locks_game_before_creating_session(monkeypatch):
    from app import guided_review_commands

    statements = []
    finding = {"id": "finding-one", "ply": 1, "kind": "major mistake",
               "confidence": 1.0, "evidence_json": json.dumps({"fen":
                   "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                   "loss_cp": 120})}

    class Database:
        def execute(self, statement, parameters=()):
            statements.append((statement, parameters))
            if "FROM imported_games" in statement:
                return SimpleNamespace(fetchone=lambda: {"id": "game-one", "analysis_version": 1})
            if "SELECT id FROM guided_review_sessions" in statement:
                return SimpleNamespace(fetchone=lambda: None)
            if "SELECT * FROM game_findings" in statement:
                return SimpleNamespace(fetchall=lambda: [finding])
            return SimpleNamespace()

    monkeypatch.setattr(guided_review_commands, "read_session_from_database",
                        lambda _database, session_id: {"id": session_id})
    result = guided_review_commands.start_review(Database(), {"game_id": "game-one"})
    assert result["id"]
    assert "FOR UPDATE" in statements[0][0]
    insert = next(parameters for statement, parameters in statements
                  if "INSERT INTO guided_review_sessions" in statement)
    assert json.loads(insert[3]) == ["finding-one"]


def test_postgres_guided_review_attempt_locks_session_before_advancing(monkeypatch):
    from app import guided_review_commands

    statements = []
    finding = {"id": "finding-one", "kind": "repertoire lapse", "ply": 1,
               "motif": None, "confidence": 1.0,
               "evidence_json": json.dumps({
                   "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                   "expected": ["e2e4"],
               })}

    class Database:
        def execute(self, statement, parameters=()):
            statements.append((statement, parameters))
            if "SELECT * FROM guided_review_sessions" in statement:
                return SimpleNamespace(fetchone=lambda: {
                    "id": "session-one", "status": "active", "current_index": 0,
                    "finding_ids_json": '["finding-one"]',
                })
            if "SELECT * FROM game_findings" in statement:
                return SimpleNamespace(fetchone=lambda: finding)
            return SimpleNamespace()

    monkeypatch.setattr(guided_review_commands, "read_session_from_database",
                        lambda _database, session_id: {"id": session_id, "current_index": 1})
    result = guided_review_commands.submit_review_attempt(
        Database(), {"session_id": "session-one", "move_uci": "e2e4"},
    )
    assert result["correct"] is True
    assert "FOR UPDATE" in statements[0][0]
    assert any("ON CONFLICT(session_id,finding_id) DO NOTHING" in statement
               for statement, _parameters in statements)
    update = next(parameters for statement, parameters in statements
                  if "UPDATE guided_review_sessions" in statement)
    assert update[:2] == (1, "complete")
