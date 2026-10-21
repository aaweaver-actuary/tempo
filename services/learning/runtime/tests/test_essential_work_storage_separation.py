"""Essential-work decisions retain their lease, transaction and replay fences."""
from contextlib import contextmanager
import hashlib
import json
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from tempo_learning_runtime import durable_tasks as tasks, maintenance, receivers


def test_only_essential_task_kinds_reach_storage(monkeypatch):
    monkeypatch.setattr(tasks.storage, "enqueue_daily_task_in_transaction", lambda *_args, **_kw: pytest.fail("Invalid kind reached storage"))
    with pytest.raises(ValueError, match="Only essential"):
        tasks.enqueue_task_in_transaction(None, "optional", "key", {})


@pytest.mark.parametrize("state", ["queued", "leased", "retrying"])
def test_same_day_remediation_requests_rescan_without_resetting_cursor_or_lease(monkeypatch, state):
    events = []
    task = dict(id="task", generation=3, state=state, lease_token="lease",
                payload_json=json.dumps(dict(queue_date="2026-10-21", after_card="card", rescan_requested=False)))
    monkeypatch.setattr(tasks.storage, "create_or_lock_remediation_task", lambda *_args, **_kw: (task, False))
    def update(_database, retained, payload):
        events.append("rescan")
        return {**retained, "payload_json": json.dumps(payload)}
    monkeypatch.setattr(tasks.storage, "set_remediation_payload", update)
    monkeypatch.setattr(tasks.storage, "reset_remediation_task", lambda *_args, **_kw: pytest.fail("Active sweep reset"))
    monkeypatch.setattr(tasks.storage, "record_remediation_event", lambda _db, _task, event: events.append(event))
    result = tasks.enqueue_task_in_transaction(None, "remediation", "key", dict(queue_date="2026-10-21"))
    assert (result["generation"], result["state"], result["lease_token"]) == (3, state, "lease")
    assert json.loads(result["payload_json"]) == dict(queue_date="2026-10-21", after_card="card", rescan_requested=True)
    assert events == ["rescan", "rescan_requested"]


def test_older_remediation_day_is_ignored_without_an_event_or_write(monkeypatch):
    task = dict(id="task", payload_json='{"queue_date":"2026-10-22"}')
    monkeypatch.setattr(tasks.storage, "create_or_lock_remediation_task", lambda *_args, **_kw: (task, False))
    for name in ["set_remediation_payload", "reset_remediation_task", "record_remediation_event"]:
        monkeypatch.setattr(tasks.storage, name, lambda *_args, **_kw: pytest.fail("Older day mutated retained work"))
    assert tasks.enqueue_task_in_transaction(None, "remediation", "key", dict(queue_date="2026-10-21")) == task


def test_completed_remediation_restarts_with_original_defaults_and_cleared_cursor(monkeypatch):
    task = dict(id="task", generation=3, state="complete", payload_json='{"queue_date":"2026-10-21"}')
    events = []
    monkeypatch.setattr(tasks.storage, "create_or_lock_remediation_task", lambda *_args, **_kw: (task, False))
    def reset(_database, retained, initial, **options):
        assert initial == dict(queue_date="2026-10-21", after_card="", rescan_requested=False)
        assert options == dict(priority=10, max_attempts=5, delay_seconds=0, minimum_generation=0)
        events.append("reset")
        return {**retained, "generation": 4}
    monkeypatch.setattr(tasks.storage, "reset_remediation_task", reset)
    monkeypatch.setattr(tasks.storage, "record_remediation_event", lambda _db, _task, event: events.append(event))
    assert tasks.enqueue_task_in_transaction(None, "remediation", "key", dict(queue_date="2026-10-21", after_card="ignored"))["generation"] == 4
    assert events == ["reset", "enqueued"]


def test_finish_reads_late_rescan_marker_before_requeueing(monkeypatch):
    task = dict(kind="remediation", id="task", generation=3, lease_token="lease")
    monkeypatch.setattr(maintenance.storage, "leased_task_payload_for_update", lambda *_: (
        json.dumps(dict(queue_date="2026-10-21", after_card="old", rescan_requested=True)),))
    writes = []
    monkeypatch.setattr(maintenance.storage, "finish_task_slice", lambda *args: writes.append(args[2:]) or ("task",))
    maintenance.finish_slice(None, task, dict(after_card="last"), False)
    assert writes == [(dict(queue_date="2026-10-21", after_card="", rescan_requested=False), "queued", "queued")]


@pytest.mark.parametrize("missing", ["read", "write"])
def test_finish_rejects_superseded_lease_at_each_existing_fence(monkeypatch, missing):
    task = dict(kind="remediation", id="task", generation=3, lease_token="lease")
    monkeypatch.setattr(maintenance.storage, "leased_task_payload_for_update", lambda *_:
                        None if missing == "read" else ('{"queue_date":"2026-10-21"}',))
    def finish(*_args):
        assert missing == "write", "Lost lease attempted a completion write"
        return None
    monkeypatch.setattr(maintenance.storage, "finish_task_slice", finish)
    with pytest.raises(maintenance.SupersededWork):
        maintenance.finish_slice(None, task, dict(after_card="last"), True)


def test_failed_application_rolls_back_before_lease_release(monkeypatch):
    from tempo_learning_runtime import remediation_adapter
    events = []
    @contextmanager
    def connection(**options):
        events.append(("begin", options))
        try:
            yield object()
        except Exception:
            events.append("rollback")
            raise
        else:
            events.append("commit")
    monkeypatch.setattr(maintenance, "connection", connection)
    monkeypatch.setattr(maintenance.storage, "next_queued_operation", lambda *_: None)
    task = dict(kind="remediation", payload_json="{}")
    monkeypatch.setattr(maintenance, "claim", lambda *_: task)
    def fail(*_args):
        raise maintenance.SupersededWork()
    monkeypatch.setattr(remediation_adapter, "maintain_one", fail)
    monkeypatch.setattr(maintenance.storage, "release_yielded_task", lambda _db, retained:
                        events.append(("release", retained is task)))
    with pytest.raises(maintenance.SupersededWork):
        maintenance.run_once()
    assert events == [
        ("begin", dict(read_only=True, background=True)), "commit",
        ("begin", dict(background=True)), "commit",
        ("begin", dict(background=True)), "rollback",
        ("begin", dict(background=True)), ("release", True), "commit",
    ]


def test_failed_retained_operation_is_deferred_without_claiming_task(monkeypatch):
    from tempo_learning_runtime import gateway
    @contextmanager
    def connection(**_options):
        yield None
    monkeypatch.setattr(maintenance, "connection", connection)
    monkeypatch.setattr(maintenance.storage, "next_queued_operation", lambda *_: ("operation",))
    def fail(_operation, *, background):
        assert background
        raise RuntimeError("busy")
    monkeypatch.setattr(gateway, "execute", fail)
    deferred = []
    monkeypatch.setattr(maintenance.storage, "defer_queued_operation", lambda _db, operation: deferred.append(operation))
    monkeypatch.setattr(maintenance, "claim", lambda *_: pytest.fail("Retained operation also claimed work"))
    assert maintenance.run_once() is True
    assert deferred == ["operation"]


@pytest.mark.parametrize("superseded", [False, True])
def test_projection_publication_follows_fenced_completion(monkeypatch, superseded):
    from tempo_learning_database.adapters import queue_position_lock
    events = []
    @contextmanager
    def connection(**_options):
        yield None
    monkeypatch.setattr(maintenance, "connection", connection)
    monkeypatch.setattr(maintenance, "next_candidates", lambda *_: [])
    monkeypatch.setattr(maintenance.frozen_opening_order, "activation", lambda *_args, **_kw: (None, 0))
    monkeypatch.setattr(queue_position_lock, "lock_queue_date_for_position", lambda *_: events.append("position lock"))
    monkeypatch.setattr(maintenance.queue_application, "presentation_rows", lambda *_: [])
    monkeypatch.setattr(maintenance.frozen_opening_order, "queue_hash", lambda *_: "membership")
    monkeypatch.setattr(maintenance.storage, "queue_day_order", lambda *_: dict(membership_hash="membership"))
    def finish(*_args):
        events.append("completion fence")
        if superseded:
            raise maintenance.SupersededWork()
    monkeypatch.setattr(maintenance, "finish_slice", finish)
    monkeypatch.setattr(maintenance.storage, "mark_queue_projection_ready", lambda *_: events.append("ready"))
    task = dict(kind="daily_queue", payload_json='{"queue_date":"2026-10-21"}')
    if superseded:
        with pytest.raises(maintenance.SupersededWork):
            maintenance.apply_task(task)
        assert events == ["position lock", "completion fence"]
    else:
        assert maintenance.apply_task(task) is True
        assert events == ["position lock", "completion fence", "ready"]


@pytest.fixture
def delivery(monkeypatch):
    monkeypatch.setattr(receivers, "_receiver", None)
    return receivers._delivery_adapter.validate_python(dict(
        contract_version=1, workspace_id="workspace", delivery_id="delivery", evidence_id="evidence",
        evidence_revision=4, game_id="game", game_revision=4,
        encounters=[dict(encounter_id="encounter", ply=0, position_key="position", decision_id="decision", observed_move="d2d4")],
        comparisons=[], misses=[], obligations=[]))


def test_receiver_workspace_denial_precedes_receipt_reads(monkeypatch, delivery):
    monkeypatch.setattr(receivers.storage, "historical_delivery_receipt", lambda *_: pytest.fail("Denied delivery read receipts"))
    with pytest.raises(HTTPException) as error:
        receivers.receive(None, delivery, "another")
    assert error.value.status_code == 403


@pytest.mark.parametrize("changed", [False, True])
def test_legacy_receipt_replay_or_conflict_precedes_frontier_and_remediation(monkeypatch, delivery, changed):
    digest = hashlib.sha256(delivery.model_dump_json().encode()).hexdigest()
    monkeypatch.setattr(receivers.storage, "historical_delivery_receipt", lambda *_: ("changed" if changed else digest, '{"retained":true}'))
    monkeypatch.setattr(receivers.storage, "accepted_revision_frontier", lambda *_: pytest.fail("Legacy receipt consulted current authority"))
    if changed:
        with pytest.raises(HTTPException) as error:
            receivers.receive(None, delivery, "workspace")
        assert error.value.status_code == 409
    else:
        assert receivers.receive(None, delivery, "workspace") == dict(retained=True)


def test_receiver_retains_frontier_and_background_context(monkeypatch, delivery):
    from tempo_learning_runtime import remediation_adapter
    monkeypatch.setattr(receivers.storage, "historical_delivery_receipt", lambda *_: None)
    monkeypatch.setattr(receivers.storage, "accepted_revision_frontier", lambda *_: (5,))
    def receive(_db, actual, **context):
        assert actual is delivery
        assert context == dict(background=True, historical_evidence_revision=5)
        return SimpleNamespace(status="accepted", model_dump=lambda **_: dict(status="accepted"))
    monkeypatch.setattr(remediation_adapter, "receive", receive)
    assert receivers.receive(None, delivery, "workspace", background=True) == dict(status="accepted")


def test_registered_receiver_keeps_authority_without_legacy_storage(monkeypatch, delivery):
    monkeypatch.setattr(receivers.storage, "historical_delivery_receipt", lambda *_: pytest.fail("Registered receiver read legacy storage"))
    def custom(_db, actual, admission):
        assert actual is delivery
        assert admission is receivers.admit
        return SimpleNamespace(model_dump=lambda **_: dict(custom=True))
    receivers.register_evidence_receiver(custom)
    assert receivers.receive(None, delivery, "workspace") == dict(custom=True)
