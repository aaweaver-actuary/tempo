"""Fail closed before the borrowed receipt writer on existing operation gates."""
from threading import Event

import pytest

from app.product_composition.encounters import receiver
from app.services.activity_gate import activity_gate
from app.services.redis_admission_gate import BackgroundAdmissionDeferred


@pytest.mark.parametrize("blocked", ["stopped", "unknown", "foreground"])
def test_existing_operation_or_foreground_hold_prevents_all_encounter_effects(monkeypatch, blocked):
    stopped = Event()
    if blocked == "stopped":
        stopped.set()
    def admission():
        if blocked == "foreground":
            raise BackgroundAdmissionDeferred("Waiting for foreground activity")
    monkeypatch.setattr(activity_gate, "check_background_admission", admission)
    def forbidden(*args, **kwargs):
        pytest.fail("A held operation must not reach a source lock or Study writer")
    monkeypatch.setattr(receiver, "receive_captured_encounter", forbidden)
    with pytest.raises(BackgroundAdmissionDeferred):
        receiver.receive_admitted_encounter(object(), capture=None, snapshot=None, command=None,
            operation_stopped=(lambda: None) if blocked == "unknown" else stopped.is_set,
            introduction_policy=None, clock=None)


def test_positive_existing_admission_selects_only_targeted_exception(monkeypatch):
    from app.services import queue_admission_adapter
    stopped = Event()
    database, capability, policy, receipt = object(), object(), object(), object()
    order = []
    monkeypatch.setattr(activity_gate, "check_background_admission", lambda: order.append("foreground"))
    def canonical(database_argument, *, allowance, paused):
        assert database_argument is database and allowance == "encounter_exception" and paused is False
        order.append("canonical")
        return capability
    def receive(database_argument, **values):
        assert database_argument is database and values["introductions"] is capability
        assert values["introduction_policy"] is policy
        order.append("receive")
        return receipt
    monkeypatch.setattr(queue_admission_adapter, "current_owner_encounter_introductions", canonical)
    monkeypatch.setattr(receiver, "receive_captured_encounter", receive)
    assert receiver.receive_admitted_encounter(database, capture=None, snapshot=None, command=None,
        operation_stopped=stopped.is_set, introduction_policy=policy, clock=None) is receipt
    assert order == ["foreground", "canonical", "receive"]
