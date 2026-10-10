"""Recovery proof waits only for legitimate foreground admission deferrals."""

from contextlib import nullcontext
import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

from app import command_gateway, tasks
from app.services.redis_admission_gate import BackgroundAdmissionDeferred

rehearsal_specification = importlib.util.spec_from_file_location(
    "checkpoint_admission_rehearsal",
    Path(__file__).resolve().parents[2] / "scripts/check_postgres_opening_evidence.py",
)
rehearsal = importlib.util.module_from_spec(rehearsal_specification)
rehearsal_specification.loader.exec_module(rehearsal)


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


def test_checkpoint_receipt_fixture_waits_for_foreground_admission_without_altering_payload(monkeypatch):
    historical_payload = {"checkpoint": {"saved": True}, "prepared_manifest": {"obsolete": True}}
    receipt = Mock(side_effect=[BackgroundAdmissionDeferred("Waiting for foreground activity"),
                               (True, historical_payload, None, None)])
    monkeypatch.setattr(rehearsal.time, "sleep", lambda _: None)
    assert rehearsal._wait_for_checkpoint_admission(
        lambda: receipt("owned", "opening_evidence.checkpoint", historical_payload, background=True)
    ) == (True, historical_payload, None, None)
    assert receipt.call_count == 2
    for invocation in receipt.call_args_list:
        assert invocation.args == ("owned", "opening_evidence.checkpoint", historical_payload)
        assert invocation.kwargs == {"background": True}


def test_checkpoint_recovery_driver_has_bounded_admission_deadline(monkeypatch):
    claim = Mock(side_effect=BackgroundAdmissionDeferred("Waiting for foreground activity"))
    _, delivery = prepare_recovery(monkeypatch, claim)
    clock = iter([0, 11])
    monkeypatch.setattr(rehearsal.time, "monotonic", lambda: next(clock))
    with pytest.raises(AssertionError, match="Recovery never obtained foreground-idle admission"):
        rehearsal._recover_checkpoint_operation("owned")
    assert claim.call_count == 1
    delivery.assert_not_called()
