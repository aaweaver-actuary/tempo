"""Retained graph/repertoire/Study boundaries with adaptive producer source absent."""

from app.services import background_activity


def test_retired_priority_manual_control_cannot_open_storage(monkeypatch):
    def unavailable_storage(*_args, **_kwargs):
        raise AssertionError("Retired priority control opened storage")

    monkeypatch.setattr(background_activity, "connection", unavailable_storage)
    assert (
        background_activity.set_control("priority", "discarded-job", "resume") is False
    )
