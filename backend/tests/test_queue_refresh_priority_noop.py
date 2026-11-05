"""A queue without stored reasons avoids the redundant priority pass."""

import pytest

from app.services import postgres_queue_refresh as refresh


@pytest.mark.parametrize("branch", ["no_plan", "saved", "reorder"])
@pytest.mark.parametrize("has_reason", [False, True])
def test_priority_pass_only_skipped_when_no_stored_reason(
    monkeypatch, branch, has_reason
):
    calls = []
    database = object()
    day = "2026-11-03"
    rows = [{"id": 1}]
    entry = {"id": 1, "position": 1, "bucket": "opening", "kind": "new"}
    plan = {
        "frozen_token": [None, 0],
        "presentation_state": "state",
        "raw_membership_hash": "membership",
        "membership_hash": "plan",
        "seed": 1,
        "entries": None if branch == "no_plan" else [entry],
    }
    monkeypatch.setattr(
        refresh.frozen_opening_order, "activation", lambda db, lock: (None, 0)
    )
    monkeypatch.setattr(
        refresh,
        "lock_queue_date_for_position",
        lambda db, date: calls.append("date_lock"),
    )
    monkeypatch.setattr(
        refresh.queue_application, "presentation_rows", lambda db, date: rows
    )
    monkeypatch.setattr(
        refresh.queue_ordering, "_queue_presentation_state", lambda value: "state"
    )
    monkeypatch.setattr(
        refresh.presentation_storage, "current_membership", lambda db, date: rows
    )
    monkeypatch.setattr(
        refresh.queue_ordering, "_queue_membership_hash", lambda value: "membership"
    )
    monkeypatch.setattr(
        refresh.presentation_storage,
        "locked_membership_hash",
        lambda db, date: ("plan",) if branch == "saved" else None,
    )
    monkeypatch.setattr(refresh.presentation_storage, "apply_plan", lambda *args: 1)
    monkeypatch.setattr(
        refresh.presentation_storage, "save_plan", lambda *args: calls.append("save")
    )

    def check(db, date):
        assert calls[0] == "date_lock"
        calls.append("stored_reason_check")
        return has_reason

    monkeypatch.setattr(refresh.presentation_storage, "has_queue_priority", check)
    monkeypatch.setattr(
        refresh, "prioritize_queued_misses", lambda db, date: calls.append("priority")
    )
    assert refresh._publish_queue_randomization(database, day, plan)
    assert calls.count("stored_reason_check") == 1
    assert ("priority" in calls) is has_reason
    assert ("save" in calls) is (branch == "reorder")


def test_stale_presentation_fails_before_priority_check(monkeypatch):
    monkeypatch.setattr(
        refresh.frozen_opening_order, "activation", lambda db, lock: (None, 0)
    )
    monkeypatch.setattr(refresh, "lock_queue_date_for_position", lambda *args: None)
    monkeypatch.setattr(
        refresh.queue_application, "presentation_rows", lambda *args: []
    )
    monkeypatch.setattr(
        refresh.queue_ordering, "_queue_presentation_state", lambda value: "current"
    )
    monkeypatch.setattr(
        refresh.presentation_storage,
        "has_queue_priority",
        lambda *args: pytest.fail("stale plan must not inspect or publish priority"),
    )
    plan = {"frozen_token": [None, 0], "presentation_state": "stale"}
    assert not refresh._publish_queue_randomization(object(), "2026-11-03", plan)
