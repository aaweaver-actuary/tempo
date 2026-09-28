"""Regressions for PostgreSQL game-finding decisions and curation."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace


def test_postgres_finding_routes_dispatch_idempotent_foreground_commands(monkeypatch):
    from fastapi.testclient import TestClient
    from app import command_dispatch, main

    dispatched = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                        dispatched.append((name, payload, idempotency_key)) or {"id": "finding-one"})
    client = TestClient(main.app)
    curated = client.post("/api/game-findings/finding-one/curation",
                          json={"action": "skip"},
                          headers={"Idempotency-Key": "curate-one"})
    decided = client.post("/api/game-findings/finding-one/decision",
                          json={"decision": "accepted"},
                          headers={"Idempotency-Key": "decide-one"})
    assert curated.status_code == decided.status_code == 200
    assert dispatched == [
        ("game_findings.curate",
         {"finding_id": "finding-one", "request": {"action": "skip"}}, "curate-one"),
        ("game_findings.decide",
         {"finding_id": "finding-one", "request": {"decision": "accepted"}},
         "decide-one"),
    ]


def test_postgres_finding_curation_locks_source_before_changing_status():
    from app.finding_commands import curate_finding

    statements = []

    class Database:
        def execute_native(self, statement, parameters=()):
            statements.append((statement, parameters))
            return SimpleNamespace(fetchone=lambda: {
                "id": "finding-one", "kind": "tactical miss", "adaptive_excluded": 0,
            })

        def execute(self, statement, parameters=()):
            statements.append((statement, parameters))

    result = curate_finding(Database(), {
        "finding_id": "finding-one", "request": {"action": "skip"},
    })
    assert result["status"] == "pending"
    assert "FOR UPDATE OF f,g" in statements[0][0]
    assert "UPDATE game_findings" in statements[1][0]


def test_postgres_accepted_repertoire_lapse_uses_canonical_miss_and_queue_lock(monkeypatch):
    from app import finding_commands

    statements = []
    events = []

    class Database:
        def execute_native(self, statement, parameters=()):
            statements.append((statement, parameters))
            return SimpleNamespace(fetchone=lambda: {
                "id": "finding-one", "kind": "repertoire lapse", "adaptive_excluded": 0,
                "card_id": "card-one", "game_id": "game-one", "repertoire_id": "rep-one",
                "ply": 4,
            })

        def execute(self, statement, parameters=()):
            statements.append((statement, parameters))
            return SimpleNamespace(fetchone=lambda: {"id": "event-one"})

    monkeypatch.setattr(finding_commands, "prioritize_real_game_miss",
                        lambda _database, event_id: events.append(event_id) or True)
    result = finding_commands.decide_finding(Database(), {
        "finding_id": "finding-one", "request": {"decision": "accepted"},
    })
    assert result == {"id": "finding-one", "status": "accepted",
                      "scheduling": None, "queued": True}
    assert events == ["event-one"]
    assert "FOR UPDATE OF f,g" in statements[0][0]
