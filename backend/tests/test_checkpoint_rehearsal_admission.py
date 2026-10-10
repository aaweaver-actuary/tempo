"""Recovery proof waits only for legitimate foreground admission deferrals."""

from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from app import command_gateway, tasks
from app.services.redis_admission_gate import BackgroundAdmissionDeferred
from scripts import check_postgres_opening_evidence as rehearsal


def prepare_recovery(monkeypatch, claim):
    database = Mock()
    monkeypatch.setattr(rehearsal.postgres_store, "connection", lambda: nullcontext(database))
    monkeypatch.setattr(command_gateway, "claim_recoverable_operation", claim)
    delivery = Mock(return_value={"persisted": True})
    monkeypatch.setattr(tasks.execute_background_command, "run", delivery)
    monkeypatch.setattr(rehearsal.time, "sleep", lambda _: None)
    return database, delivery


def test_checkpoint_recovery_driver_waits_for_foreground_admission(monkeypatch):
    claim = Mock(side_effect=[BackgroundAdmissionDeferred("Waiting for foreground activity"),
                             {"operation_id": "owned", "background": True,
                              "command_name": "opening_evidence.checkpoint", "payload": {"saved": True}}])
    database, delivery = prepare_recovery(monkeypatch, claim)
    assert rehearsal._recover_checkpoint_operation("owned") == {"persisted": True}
    assert claim.call_count == 2
    assert database.execute_native.call_count == 1
    delivery.assert_called_once_with("owned", "opening_evidence.checkpoint", {"saved": True})


def test_checkpoint_recovery_driver_preserves_nonadmission_errors(monkeypatch):
    claim = Mock(side_effect=RuntimeError("database failure"))
    _, delivery = prepare_recovery(monkeypatch, claim)
    with pytest.raises(RuntimeError, match="database failure"):
        rehearsal._recover_checkpoint_operation("owned")
    assert claim.call_count == 1
    delivery.assert_not_called()


def test_checkpoint_recovery_driver_has_bounded_admission_deadline(monkeypatch):
    claim = Mock(side_effect=BackgroundAdmissionDeferred("Waiting for foreground activity"))
    _, delivery = prepare_recovery(monkeypatch, claim)
    clock = iter([0, 11])
    monkeypatch.setattr(rehearsal.time, "monotonic", lambda: next(clock))
    with pytest.raises(AssertionError, match="Recovery never obtained foreground-idle admission"):
        rehearsal._recover_checkpoint_operation("owned")
    assert claim.call_count == 1
    delivery.assert_not_called()
