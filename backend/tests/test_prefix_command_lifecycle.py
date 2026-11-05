"""Gateway ordering for the actual prefix descriptor's exceptional lifecycle."""

from contextlib import contextmanager, nullcontext
import json

import pytest
from fastapi import HTTPException
from psycopg.errors import SerializationFailure
from app import command_gateway as gateway, prefix_evaluation_api
from app.contracts.prefix_errors import PrefixEvaluationForegroundDeferred
from app.services import prefix_transition_application as owner


@pytest.fixture
def boundary(monkeypatch):
    events, state = [], {"receipt": None, "result": {"status": "pending"}}
    name = owner.COMMAND
    assert (
        gateway._registry.transition_lifecycles[name]
        is owner.PREFIX_TRANSITION_COMMAND.transition_lifecycle
    )

    @contextmanager
    def writer(*_):
        events.append("writer")
        yield object()
        events.append("close")

    monkeypatch.setattr(gateway, "_writer_connection", writer)
    monkeypatch.setattr(gateway.storage, "receipt_writer", lambda db: db)
    monkeypatch.setattr(
        gateway.storage, "lock_operation", lambda *_: events.append("lock")
    )
    monkeypatch.setattr(
        gateway.storage, "insert_pending", lambda *_: events.append("insert")
    )
    monkeypatch.setattr(
        gateway.storage,
        "locked_publication",
        lambda _raw, operation: (
            state["receipt"]
            or (name, gateway.request_digest(name, {}), "pending", None, None, None)
        ),
    )
    monkeypatch.setattr(
        gateway.storage, "begin_handler", lambda *_: events.append("begin")
    )
    monkeypatch.setattr(
        gateway.storage, "rollback_handler", lambda *_: events.append("rollback")
    )
    monkeypatch.setattr(
        gateway.storage, "release_handler", lambda *_: events.append("release")
    )
    monkeypatch.setattr(
        gateway.storage, "keep_transition_pending", lambda *_: events.append("pending")
    )
    monkeypatch.setattr(
        gateway.storage, "block_transition", lambda *_: events.append("blocked")
    )
    monkeypatch.setattr(
        gateway.storage, "fail_operation", lambda *_: events.append("failed")
    )
    monkeypatch.setattr(
        gateway.storage, "complete_operation", lambda *_: events.append("complete")
    )
    monkeypatch.setitem(gateway._preparers, name, lambda payload: payload)
    monkeypatch.setitem(gateway._handlers, name, lambda *_: state["result"])
    monkeypatch.setattr(
        owner.PrefixTransitionLifecycle, "writer_admission", lambda _: nullcontext()
    )
    return events, state


def test_prefix_pending_and_definitive_failure_order(boundary, monkeypatch):
    events, state = boundary
    assert gateway.execute_command("op", owner.COMMAND, {}) == {"status": "pending"}
    assert events == [
        "writer",
        "lock",
        "insert",
        "begin",
        "release",
        "pending",
        "close",
    ]
    events.clear()

    def fail(*_):
        raise HTTPException(409, "bad")

    monkeypatch.setitem(gateway._handlers, owner.COMMAND, fail)
    monkeypatch.setattr(
        owner,
        "reject_unactivated_application",
        lambda *_: events.append("reject") or True,
    )
    assert gateway.execute_command("op", owner.COMMAND, {}) is None
    assert events == [
        "writer",
        "lock",
        "insert",
        "begin",
        "rollback",
        "reject",
        "blocked",
        "close",
    ]
    events.clear()
    monkeypatch.setattr(
        owner,
        "reject_unactivated_application",
        lambda *_: events.append("reject") or False,
    )
    assert gateway.execute_command("op", owner.COMMAND, {}) is None
    assert events == [
        "writer",
        "lock",
        "insert",
        "begin",
        "rollback",
        "reject",
        "failed",
        "close",
    ]


def test_prefix_delayed_503_follows_receipt_and_replay_fence(boundary, monkeypatch):
    events, state = boundary

    def unavailable(_):
        raise HTTPException(503, "busy")

    monkeypatch.setitem(gateway._preparers, owner.COMMAND, unavailable)
    state["receipt"] = (
        owner.COMMAND,
        gateway.request_digest(owner.COMMAND, {}),
        "complete",
        json.dumps({"saved": True}),
        None,
        None,
    )
    assert gateway.execute_command("op", owner.COMMAND, {}) == {"saved": True}
    assert events == ["writer", "lock", "insert", "close"]
    events.clear()
    state["receipt"] = (
        owner.COMMAND,
        gateway.request_digest(owner.COMMAND, {}),
        "executing",
        None,
        None,
        "other",
    )
    assert (
        gateway.execute_command("op", owner.COMMAND, {}, attempt_token="stale") is None
    )
    assert events == ["writer", "lock", "insert", "close"]
    events.clear()
    state["receipt"] = None
    with pytest.raises(SerializationFailure):
        gateway.execute_command("op", owner.COMMAND, {})
    assert events == ["writer", "lock", "insert", "begin"]


def test_public_foreground_exception_identity_and_all_preparers_defer_before_writer(
    boundary, monkeypatch
):
    events, _ = boundary
    assert (
        prefix_evaluation_api.PrefixEvaluationForegroundDeferred
        is PrefixEvaluationForegroundDeferred
    )

    def defer(_):
        raise PrefixEvaluationForegroundDeferred()

    monkeypatch.setitem(gateway._handlers, "other.command", lambda *_: None)
    monkeypatch.setitem(gateway._preparers, "other.command", defer)
    with pytest.raises(gateway.BackgroundAdmissionDeferred):
        gateway.execute_command("op", "other.command", {})
    assert events == []
