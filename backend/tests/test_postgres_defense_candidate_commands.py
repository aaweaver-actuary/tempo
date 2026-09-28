"""Defensive candidate decisions stay inside one foreground command receipt."""

from __future__ import annotations

import pytest

from app import command_dispatch, defense_commands, main, postgres_store


@pytest.mark.parametrize(
    ("action", "expected_status", "route_name"),
    [
        ("dismiss", "dismissed", "dismiss_defensive_threat_candidate"),
        ("approve", "approved", "approve_defensive_threat_candidate"),
        ("train-now", "queued", "train_defensive_threat_candidate_now"),
        ("pause", "paused", "pause_defensive_threat_candidate"),
        ("resume", "eligible", "resume_defensive_threat_candidate"),
    ],
)
def test_postgres_defense_candidate_action_uses_one_receipt_and_followup(
    monkeypatch, action, expected_status, route_name,
):
    events = []
    database = object()
    monkeypatch.setattr(defense_commands, "dismiss_defense_candidate",
                        lambda candidate_id, *, write_database:
                        events.append(("dismiss", candidate_id, write_database)))
    monkeypatch.setattr(defense_commands, "approve_defense_candidate",
                        lambda candidate_id, *, write_database:
                        events.append(("approve", candidate_id, write_database)) or "card-one")
    monkeypatch.setattr(defense_commands, "train_defense_candidate_now",
                        lambda candidate_id, *, write_database:
                        events.append(("train-now", candidate_id, write_database)) or "card-one")
    monkeypatch.setattr(defense_commands, "pause_defense_candidate",
                        lambda candidate_id, paused, *, write_database:
                        events.append(("pause" if paused else "resume", candidate_id,
                                       write_database)))
    monkeypatch.setattr(defense_commands, "request_queue_refresh_in_transaction",
                        lambda write_database, _day: events.append(("queue", write_database)))
    monkeypatch.setattr(defense_commands, "enqueue_compact_postgres_task_in_transaction",
                        lambda write_database, kind, key, payload, *, priority:
                        events.append(("admission", write_database, kind, key, payload,
                                       priority)))

    result = defense_commands.change_candidate_state(
        database, {"candidate_id": "candidate-one", "action": action},
    )
    assert result["status"] == expected_status
    assert events[0] == (action, "candidate-one", database)
    assert any(event[0] == "queue" for event in events) == (action == "approve")
    assert any(event[0] == "admission" for event in events) == (action == "resume")

    monkeypatch.setattr(postgres_store, "configured", lambda: True)
    monkeypatch.setattr(command_dispatch, "dispatch_command",
                        lambda name, payload, *, idempotency_key:
                        {"name": name, "payload": payload, "operation_id": idempotency_key})
    assert getattr(main, route_name)("candidate-one", "decision-one") == {
        "name": "defense.candidate.change",
        "payload": {"candidate_id": "candidate-one", "action": action},
        "operation_id": "decision-one",
    }
