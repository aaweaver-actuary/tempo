"""Regressions for read-only finding-card previews and foreground saves."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

from fastapi import HTTPException
import pytest


def test_postgres_finding_card_preview_reads_only_and_save_dispatches(monkeypatch):
    from fastapi.testclient import TestClient
    from app import command_dispatch, finding_card_commands, main

    observed = []
    monkeypatch.setattr(main.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(main.activity_gate, "foreground", lambda: nullcontext())
    monkeypatch.setattr(main, "read_connection", lambda: nullcontext("reader"))
    monkeypatch.setattr(main, "connection", lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("API must not write a finding card")))
    monkeypatch.setattr(finding_card_commands, "preview_finding_card",
                        lambda database, finding_id, request:
                        observed.append((database, finding_id, request.save)) or
                        {"saved": False, "preview": {"moves": ["e2e4"]}})
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                        observed.append((name, payload, idempotency_key)) or
                        {"saved": True, "card_id": "card-one"})
    client = TestClient(main.app)
    preview = client.post("/api/game-findings/finding-one/card", json={"save": False})
    saved = client.post("/api/game-findings/finding-one/card", json={"save": True},
                        headers={"Idempotency-Key": "save-finding-one"})
    assert preview.status_code == saved.status_code == 200
    assert observed == [
        ("reader", "finding-one", False),
        ("game_findings.card.save",
         {"finding_id": "finding-one", "request": {
             "save": True, "starting_fen": None, "moves": None, "trained_color": None,
         }}, "save-finding-one"),
    ]


def test_postgres_finding_card_save_rejects_stale_tactical_opportunity_before_writing():
    from app.finding_card_commands import save_finding_card

    statements = []

    class Database:
        def execute(self, statement, parameters=()):
            statements.append(statement)
            if "SELECT game_id,source_opportunity_id" in statement:
                row = {"game_id": "game-one", "source_opportunity_id": "opportunity-one"}
            elif "SELECT f.*,g.color" in statement:
                row = {"id": "finding-one", "kind": "tactical miss", "adaptive_excluded": 0,
                       "evidence_json": '{"fen":"8/8/8/8/8/8/4K3/7k w - - 0 1"}',
                       "source_opportunity_id": "opportunity-one", "opportunity_active": 0,
                       "opportunity_analysis_version": 1, "game_analysis_version": 2,
                       "confidence": 1.0}
            else:
                row = {"id": "locked"}
            return SimpleNamespace(fetchone=lambda: row)

    with pytest.raises(HTTPException) as error:
        save_finding_card(Database(), {
            "finding_id": "finding-one", "request": {"save": True},
        })
    assert error.value.status_code == 409
    assert all(not statement.lstrip().startswith(("INSERT", "UPDATE"))
               for statement in statements)
