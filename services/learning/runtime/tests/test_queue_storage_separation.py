"""Queue decisions retain issuance, refresh and positioned-retry safeguards."""
from datetime import date
import json
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from tempo_learning_runtime import queue_application as queue, queue_commands as commands
from tempo_learning_runtime.review_conflicts import ReviewConflict


def test_presentation_labels_use_supplied_history_cycle_and_eligibility(monkeypatch):
    rows = [dict(id=1, card_id="explicit", admission_kind="explicit"),
            dict(id=2, card_id="reviewed", admission_kind="new"),
            dict(id=3, card_id="repeat", admission_kind="new", cycle=2),
            dict(id=4, card_id="new", admission_kind="review")]
    monkeypatch.setattr(queue.storage, "presentation_facts", lambda *_: (rows, {"reviewed"}, {1, 3}))
    result = queue.presentation_rows(None, "2026-10-21")
    assert [row["admission_kind"] for row in result] == ["explicit", "review", "review", "new"]
    assert [row["eligible"] for row in result] == [True, False, True, False]


@pytest.mark.parametrize("after,found", [(None, None), (-3, (2,)), (8, None)])
def test_requeue_retains_capture_placement_shift_and_preservation_order(monkeypatch, after, found):
    from tempo_learning_runtime import review_service
    events = []
    monkeypatch.setattr(review_service, "capture_daily_queue_order", lambda *_: events.append("capture") or "previous")
    monkeypatch.setattr(queue.storage, "next_cycle", lambda *_: events.append("cycle") or 3)
    monkeypatch.setattr(queue.storage, "next_position", lambda *_: events.append("last position") or 7)
    def offset(_db, day, index, _reason):
        assert (day, index) == ("2026-10-21", max(0, after))
        events.append("offset")
        return found
    monkeypatch.setattr(queue.storage, "available_position_at_offset", offset)
    monkeypatch.setattr(queue.storage, "shift_queued_positions", lambda _db, _day, position: events.append(("shift", position)))
    def insert(_db, day, card, cycle, position, attempt):
        assert (day, card, cycle, attempt) == ("2026-10-21", "card", 3, "guided")
        events.append(("insert", position))
        return 19
    monkeypatch.setattr(queue.storage, "insert_repeated_entry", insert)
    def preserve(_db, _day, *, previous):
        assert previous == "previous"
        events.append("preserve")
    monkeypatch.setattr(queue, "preserve_daily_queue_order", preserve)
    assert queue.requeue(None, "2026-10-21", "card", after, "guided") == 19
    position = found[0] if found else 7
    assert events == ["capture", "cycle"] + (["offset"] if after is not None else []) + (
        ["last position"] if not found else []) + ([("shift", position)] if after is not None else []) + [("insert", position), "preserve"]


@pytest.fixture
def command_day(monkeypatch):
    monkeypatch.setattr(commands, "date", SimpleNamespace(today=lambda: date(2026, 10, 21), fromisoformat=date.fromisoformat))


def test_unavailable_attempt_stops_before_origin_validation(monkeypatch, command_day):
    from tempo_learning_runtime import queue_attempt_origins
    monkeypatch.setattr(commands.storage, "available_attempt", lambda *_: None)
    monkeypatch.setattr(queue_attempt_origins, "validate_failure_marker", lambda *_: pytest.fail("Unavailable attempt read origin"))
    with pytest.raises(ReviewConflict) as error:
        commands.validate_available_attempt(None, 4, "card", 2)
    assert error.value.code == "queue_attempt_inactive"


@pytest.mark.parametrize("issued", [False, True])
def test_identified_attempt_requires_current_head_or_retained_issuance(monkeypatch, command_day, issued):
    from tempo_learning_runtime import queue_attempt_origins
    events = []
    monkeypatch.setattr(commands.storage, "available_attempt", lambda *_: events.append("available") or dict(id=4))
    monkeypatch.setattr(queue_attempt_origins, "validate_failure_marker", lambda *_: events.append("definition fence"))
    monkeypatch.setattr(commands.storage, "queue_head", lambda *_: events.append("head") or dict(id=9))
    monkeypatch.setattr(commands.storage, "issued_origin", lambda *_: events.append("issued origin") or ((1,) if issued else None))
    if issued:
        assert commands.validate_available_attempt(None, 4, "card", 2) is None
    else:
        with pytest.raises(ReviewConflict) as error:
            commands.validate_available_attempt(None, 4, "card", 2)
        assert error.value.code == "queue_attempt_inactive"
    assert events == ["available", "definition fence", "head", "issued origin"]


def test_wrong_day_head_request_has_no_storage_effect(monkeypatch, command_day):
    monkeypatch.setattr(commands, "_lock_attempt_card", lambda *_: pytest.fail("Wrong day acquired a lock"))
    assert commands.issue_queue_head(None, dict(entry_id=4, queue_date="2026-10-20")) == dict(issued=False)


@pytest.mark.parametrize("prior", [0, 1])
def test_head_issuance_binds_once_after_validation_before_marker(monkeypatch, command_day, prior):
    from tempo_learning_runtime import remediation_adapter
    events = []
    monkeypatch.setattr(commands, "_lock_attempt_card", lambda *_: events.append("card lock"))
    monkeypatch.setattr(commands.storage, "lock_issue_queue_position", lambda *_: events.append("position lock"))
    monkeypatch.setattr(commands.storage, "queue_head", lambda *_: events.append("head") or dict(id=4))
    monkeypatch.setattr(commands, "validate_available_attempt", lambda *_: events.append("validate"))
    monkeypatch.setattr(commands.storage, "issued_head_flag", lambda *_: (prior,))
    monkeypatch.setattr(remediation_adapter, "bind_issued_attempt", lambda *_: events.append("bind"))
    monkeypatch.setattr(commands.storage, "mark_head_issued", lambda *_: events.append("mark") or 1)
    assert commands.issue_queue_head(None, dict(entry_id=4, queue_date="2026-10-21", card_id="card", expected_revision=2)) == dict(issued=True)
    assert events == ["card lock", "position lock", "head", "validate"] + (["bind"] if not prior else []) + ["mark"]


@pytest.mark.parametrize("changed", [0, 1])
def test_failed_marker_preserves_lost_attempt_conflict(monkeypatch, changed):
    events = []
    monkeypatch.setattr(commands, "_lock_attempt_card", lambda *_: events.append("lock"))
    monkeypatch.setattr(commands, "validate_available_attempt", lambda *_: events.append("validate"))
    monkeypatch.setattr(commands.storage, "mark_attempt_failed", lambda *_: events.append("mark") or changed)
    if changed:
        assert commands.mark_attempt_failed(None, dict(entry_id=4)) == dict(attempt_failed=True)
    else:
        with pytest.raises(ReviewConflict) as error:
            commands.mark_attempt_failed(None, dict(entry_id=4))
        assert error.value.code == "queue_attempt_inactive"
    assert events == ["lock", "validate", "mark"]


def test_burial_keeps_card_position_validation_and_order_provenance(monkeypatch, command_day):
    events = []
    monkeypatch.setattr(commands, "_lock_attempt_card", lambda *_: events.append("card lock"))
    monkeypatch.setattr(commands, "lock_queue_date_for_position", lambda *_: events.append("position lock"))
    monkeypatch.setattr(commands, "validate_available_attempt", lambda *_: events.append("validate"))
    monkeypatch.setattr(commands, "capture_daily_queue_order", lambda *_: events.append("capture") or "previous")
    monkeypatch.setattr(commands.storage, "bury_card_entries", lambda *_: events.append("bury"))
    def preserve(_db, _day, *, previous):
        assert previous == "previous"
        events.append("preserve")
    monkeypatch.setattr(commands, "preserve_daily_queue_order", preserve)
    assert commands.bury_queue_entry(None, dict(entry_id=4)) == dict(buried=True, queue_entry_id=4)
    assert events == ["card lock", "position lock", "validate", "capture", "bury", "preserve"]


def test_invalid_refresh_date_rejects_before_task_read(monkeypatch, command_day):
    monkeypatch.setattr(commands.storage, "current_refresh_task", lambda *_: pytest.fail("Invalid date read task"))
    with pytest.raises(HTTPException) as error:
        commands.ensure_current_queue(None, dict(queue_date="invalid"))
    assert error.value.status_code == 422


def test_ready_projection_returns_without_refresh_mutation(monkeypatch, command_day):
    monkeypatch.setattr(commands.storage, "current_refresh_task", lambda *_: None)
    def projection(_db, day, locked):
        assert (day, locked) == ("2026-10-21", False)
        return dict(state="ready", refresh_pending=False)
    monkeypatch.setattr(commands.storage, "refresh_projection", projection)
    monkeypatch.setattr(commands, "request_queue_refresh_in_transaction", lambda *_: pytest.fail("Ready projection enqueued work"))
    assert commands.ensure_current_queue(None, dict(queue_date="2026-10-21")) == dict(queue_date="2026-10-21", refresh_pending=False)


@pytest.mark.parametrize("state", ["queued", "leased", "retrying"])
def test_active_same_day_task_retains_identity_and_failure_detail(monkeypatch, command_day, state):
    events = []
    task = dict(id="task", state=state, payload_json=json.dumps(dict(queue_date="2026-10-21")), last_error="yielded")
    monkeypatch.setattr(commands.storage, "current_refresh_task", lambda *_: events.append("task lock") or task)
    def projection(_db, day, locked):
        assert (day, locked) == ("2026-10-21", True)
        events.append("projection lock")
        return None
    monkeypatch.setattr(commands.storage, "refresh_projection", projection)
    monkeypatch.setattr(commands.storage, "retain_refreshing_projection", lambda _db, _day, error: events.append(("refreshing", error)))
    monkeypatch.setattr(commands, "request_queue_refresh_in_transaction", lambda *_: pytest.fail("Active task replaced"))
    assert commands.ensure_current_queue(None, dict(queue_date="2026-10-21")) == dict(queue_date="2026-10-21", refresh_pending=True, task_id="task")
    assert events == ["task lock", "projection lock", ("refreshing", "yielded")]


@pytest.mark.parametrize("state,day", [("complete", "2026-10-21"), ("queued", "2026-10-20")])
def test_done_or_other_day_task_requests_existing_refresh_capability(monkeypatch, command_day, state, day):
    monkeypatch.setattr(commands.storage, "current_refresh_task", lambda *_: dict(state=state, payload_json=json.dumps(dict(queue_date=day))))
    monkeypatch.setattr(commands.storage, "refresh_projection", lambda *_: None)
    calls = []
    monkeypatch.setattr(commands, "request_queue_refresh_in_transaction", lambda _db, day: calls.append(day) or dict(id="new"))
    assert commands.ensure_current_queue(None, dict(queue_date="2026-10-21"))["task_id"] == "new"
    assert calls == ["2026-10-21"]
