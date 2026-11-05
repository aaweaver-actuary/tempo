"""Settings descriptor adoption through the real gateway and receipt flow."""

from contextlib import contextmanager, nullcontext
from dataclasses import replace
import copy
import json

import pytest
from fastapi import HTTPException

from app import command_gateway as gateway, settings_commands as settings
from app.contracts.transport.settings import Settings
from app.services import queue_refresh_wakeup, study_priority


@pytest.fixture
def runtime(monkeypatch):
    receipts, effects, wakes = {}, [], []
    raw = object()
    monkeypatch.setattr(gateway, "_writer_connection", lambda *_: nullcontext(raw))
    monkeypatch.setattr(gateway.storage, "receipt_writer", lambda db: db)
    monkeypatch.setattr(gateway.storage, "lock_operation", lambda *_: None)

    def insert(_raw, operation, name, digest):
        receipts.setdefault(operation, [name, digest, "pending", None, None, None])

    monkeypatch.setattr(gateway.storage, "insert_pending", insert)
    monkeypatch.setattr(
        gateway.storage,
        "locked_publication",
        lambda _raw, operation: receipts[operation],
    )
    monkeypatch.setattr(gateway.storage, "begin_handler", lambda *_: None)
    monkeypatch.setattr(
        gateway.storage, "rollback_handler", lambda *_: effects.append("rollback")
    )
    monkeypatch.setattr(gateway.storage, "release_handler", lambda *_: None)

    def complete(_raw, value, operation):
        receipts[operation][2:4] = ["complete", value]

    def fail(_raw, value, operation):
        receipts[operation][2] = "failed"
        receipts[operation][4] = value

    monkeypatch.setattr(gateway.storage, "complete_operation", complete)
    monkeypatch.setattr(gateway.storage, "fail_operation", fail)
    monkeypatch.setattr(
        settings.storage, "locked_settings", lambda _: Settings().model_dump()
    )
    monkeypatch.setattr(
        settings.storage, "store_settings", lambda *_: effects.append("settings")
    )
    monkeypatch.setattr(
        settings.storage,
        "store_provider_account",
        lambda _db, provider, name: effects.append((provider, name)),
    )
    monkeypatch.setattr(
        settings.storage,
        "remove_provider_account",
        lambda _db, provider: effects.append(("remove", provider)),
    )

    def request_queue(*_):
        effects.append("queue")
        queue_refresh_wakeup.mark_queue_refresh_requested()

    monkeypatch.setattr(settings, "request_queue_refresh_in_transaction", request_queue)
    monkeypatch.setattr(
        queue_refresh_wakeup, "wake_queue_refresh", lambda: wakes.append("wake")
    )
    monkeypatch.setattr(study_priority, "refresh_policy", lambda: None)
    return receipts, effects, wakes


def payload():
    return {
        "settings": Settings(
            new_cards_per_day=12, lichess_username=" user "
        ).model_dump(),
        "supplied_fields": ["new_cards_per_day", "lichess_username"],
    }


def test_settings_descriptor_replay_preserves_original_identity_and_effects(
    runtime, monkeypatch
):
    receipts, effects, wakes = runtime
    original = payload()
    snapshot = copy.deepcopy(original)
    assert "settings.update" in gateway._registry.preparers
    first = gateway.execute_command("settings-op", "settings.update", original)
    assert original == snapshot
    assert first["new_cards_per_day"] == 12
    assert effects == [
        "settings",
        ("lichess", "user"),
        ("remove", "chess.com"),
        "queue",
    ]
    assert wakes == ["wake"]
    original_prepare = gateway._preparers["settings.update"]

    def changed_preparation(value):
        prepared = original_prepare(value)
        return replace(prepared, settings=Settings(new_cards_per_day=42))

    monkeypatch.setitem(gateway._preparers, "settings.update", changed_preparation)
    assert gateway.execute_command("settings-op", "settings.update", original) == first
    assert effects.count("settings") == 1 and wakes == ["wake"]
    changed = copy.deepcopy(original)
    changed["settings"]["new_cards_per_day"] = 13
    with pytest.raises(gateway.CommandConflict):
        gateway.execute_command("settings-op", "settings.update", changed)
    assert effects.count("settings") == 1
    assert receipts["settings-op"][1] == gateway.request_digest(
        "settings.update", original
    )


def test_completed_settings_receipt_survives_invalid_fresh_preparation(
    runtime, monkeypatch
):
    _, effects, _ = runtime
    original = payload()
    first = gateway.execute_command("completed-op", "settings.update", original)

    def invalid(_):
        raise ValueError("changed preparation")

    monkeypatch.setitem(gateway._preparers, "settings.update", invalid)
    assert gateway.execute_command("completed-op", "settings.update", original) == first
    assert effects.count("settings") == 1


@pytest.mark.parametrize(
    "invalid, status, detail",
    [
        (
            {"settings": {}, "supplied_fields": ["unknown"]},
            422,
            "Unknown settings field",
        ),
        ({"settings": {"new_cards_per_day": -1}, "supplied_fields": []}, 500, None),
    ],
)
def test_invalid_settings_still_records_definitive_failure(
    runtime, invalid, status, detail
):
    receipts, effects, wakes = runtime
    assert gateway.execute_command("invalid-op", "settings.update", invalid) is None
    error = json.loads(receipts["invalid-op"][4])
    assert receipts["invalid-op"][2] == "failed" and error["status_code"] == status
    if detail is not None:
        assert error["detail"] == detail
    assert effects == ["rollback"] and wakes == []


def test_failed_settings_queue_request_does_not_wake(runtime, monkeypatch):
    receipts, effects, wakes = runtime

    def failure(*_):
        queue_refresh_wakeup.mark_queue_refresh_requested()
        raise HTTPException(503, "queue unavailable")

    monkeypatch.setattr(settings, "request_queue_refresh_in_transaction", failure)
    assert gateway.execute_command("failed-op", "settings.update", payload()) is None
    error = json.loads(receipts["failed-op"][4])
    assert error["status_code"] == 503 and error["detail"] == "queue unavailable"
    assert effects[-1] == "rollback" and wakes == []


def test_settings_commit_policy_hook_precedes_wake_and_replays(runtime, monkeypatch):
    events = []

    @contextmanager
    def writer(*_):
        events.append("open")
        yield object()
        events.extend(["commit", "close"])

    monkeypatch.setattr(gateway, "_writer_connection", writer)
    monkeypatch.setattr(
        study_priority, "refresh_policy", lambda: events.append("policy")
    )
    monkeypatch.setattr(
        queue_refresh_wakeup, "wake_queue_refresh", lambda: events.append("wake")
    )
    first = gateway.execute_command("ordered-op", "settings.update", payload())
    assert events == ["open", "commit", "close", "policy", "wake"]
    events.clear()
    assert gateway.execute_command("ordered-op", "settings.update", payload()) == first
    assert events == ["open", "commit", "close", "policy"]


def test_failed_settings_skips_policy_and_wake(runtime, monkeypatch):
    events = []
    monkeypatch.setattr(
        study_priority, "refresh_policy", lambda: events.append("policy")
    )
    monkeypatch.setattr(
        queue_refresh_wakeup, "wake_queue_refresh", lambda: events.append("wake")
    )
    assert (
        gateway.execute_command(
            "invalid-hook-op",
            "settings.update",
            {"settings": {}, "supplied_fields": ["unknown"]},
        )
        is None
    )
    assert events == []


def test_settings_policy_hook_exception_occurs_after_commit_before_wake(
    runtime, monkeypatch
):
    events = []

    @contextmanager
    def writer(*_):
        yield object()
        events.extend(["commit", "close"])

    def fail_policy():
        events.append("policy")
        raise RuntimeError("policy unavailable")

    monkeypatch.setattr(gateway, "_writer_connection", writer)
    monkeypatch.setattr(study_priority, "refresh_policy", fail_policy)
    monkeypatch.setattr(
        queue_refresh_wakeup, "wake_queue_refresh", lambda: events.append("wake")
    )
    with pytest.raises(RuntimeError, match="policy unavailable"):
        gateway.execute_command("hook-error-op", "settings.update", payload())
    assert events == ["commit", "close", "policy"]
    assert runtime[0]["hook-error-op"][2] == "complete"
