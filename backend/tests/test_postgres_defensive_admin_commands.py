"""Defensive audit and backfill command receipts retain their task IDs."""

from __future__ import annotations

import pytest

from app import command_dispatch, defensive_admin_commands, main, postgres_store


@pytest.mark.parametrize(
    ("command", "kind", "deduplication_key", "payload", "priority"),
    [
        ("audit", "defensive_threat_report_audit", "saved-reports", {"cursor": ""}, 135),
        ("backfill", "defensive_threat_backfill", "analyzed-games",
         {"phase": "games", "cursor": ""}, 160),
    ],
)
def test_postgres_defensive_admin_receipt_matches_durable_task(
    monkeypatch, command, kind, deduplication_key, payload, priority,
):
    enqueued = []
    monkeypatch.setattr(defensive_admin_commands, "enqueue_task_in_transaction",
                        lambda _database, task_kind, key, task_payload, *, priority:
                        enqueued.append((task_kind, key, task_payload, priority))
                        or {"id": "task-one"})
    handler = (defensive_admin_commands.request_defensive_audit
               if command == "audit" else defensive_admin_commands.request_defensive_backfill)
    assert handler(object(), {}) == {"status": "queued", "task_id": "task-one"}
    assert enqueued == [(kind, deduplication_key, payload, priority)]

    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, _payload, *, idempotency_key:
                        {"name": name, "operation_id": idempotency_key})
    route = (main.audit_defensive_threat_reports if command == "audit"
             else main.backfill_defensive_threats)
    assert route("operation-one") == {
        "name": f"defensive.{command}", "operation_id": "operation-one",
    }
