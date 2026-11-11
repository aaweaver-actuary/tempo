"""Registration ownership and type correlation without command execution infrastructure."""

from dataclasses import FrozenInstanceError
from pathlib import Path
import os
import subprocess
import sys

import pytest
from app.contracts.command_registry import CommandDescriptor, CommandRegistry


def test_typed_descriptor_correlates_original_prepared_and_result():
    calls = []

    def decode(payload: dict) -> int:
        calls.append(("decode", payload))
        return int(payload["value"])

    def prepare(value: int) -> str:
        calls.append(("prepare", value))
        return f"prepared:{value}"

    def apply(transaction: list, value: str) -> int:
        transaction.append(value)
        return len(value)

    descriptor: CommandDescriptor[list, int, str, int] = CommandDescriptor(
        "typed", decode, prepare, apply
    )
    registry = CommandRegistry()
    registry.register(descriptor)
    original = {"value": "4", "untouched": None}
    prepared = registry.preparers["typed"](original)
    transaction = []
    assert registry.handlers["typed"](transaction, prepared) == len("prepared:4")
    assert transaction == ["prepared:4"]
    assert original == {"value": "4", "untouched": None}
    assert calls == [("decode", original), ("prepare", 4)]
    with pytest.raises(FrozenInstanceError):
        descriptor.name = "changed"
    with pytest.raises(ValueError, match="Duplicate command: typed"):
        registry.register(descriptor)


def test_explicit_registries_do_not_share_registrations():
    first, second = CommandRegistry(), CommandRegistry()
    descriptor: CommandDescriptor[list, int, str, int] = CommandDescriptor(
        "typed",
        lambda payload: int(payload["value"]),
        str,
        lambda tx, value: tx.append(value) or len(value),
    )
    first.register(descriptor)
    assert "typed" in first.handlers and "typed" not in second.handlers
    second.register(descriptor)
    first.handlers.pop("typed")
    assert "typed" in second.handlers


def test_after_commit_is_descriptor_owned_and_legacy_names_are_noops():
    observed = []
    registry = CommandRegistry()
    registry.register(
        CommandDescriptor(
            "typed",
            lambda p: p["value"],
            str,
            lambda _tx, value: len(value),
            observed.append,
        )
    )
    registry.after_commit("typed", 4)
    registry.after_commit("legacy", 7)
    assert observed == [4]
    registry.handlers.pop("typed")
    registry.register(
        CommandDescriptor(
            "typed", lambda p: p["value"], str, lambda _tx, value: len(value)
        )
    )
    registry.after_commit("typed", 5)
    assert observed == [4]
    assert CommandRegistry()._after_commit == {}


def test_default_legacy_registration_and_executor_maps_are_registry_owned(monkeypatch):
    from app import command_gateway as gateway

    assert gateway._handlers is gateway._registry.handlers
    assert gateway._preparers is gateway._registry.preparers
    handler = lambda *_: None
    prepare = lambda payload: payload
    gateway.register_command("test.registry.contract", handler, prepare=prepare)
    try:
        assert gateway._registry.handlers["test.registry.contract"] is handler
        assert gateway._registry.preparers["test.registry.contract"] is prepare
        with pytest.raises(
            ValueError, match="Duplicate command: test.registry.contract"
        ):
            gateway.register_command("test.registry.contract", handler)
    finally:
        gateway._handlers.pop("test.registry.contract")
        gateway._preparers.pop("test.registry.contract")


def test_descriptor_registration_shares_legacy_execution_maps():
    from app import command_gateway as gateway
    from tempo_storage.postgres import PostgresConnection

    observed = []

    def apply(tx: PostgresConnection, value: str) -> int:
        observed.append((tx, value))
        return len(value)

    descriptor: CommandDescriptor[PostgresConnection, int, str, int] = (
        CommandDescriptor(
            "test.typed.contract",
            lambda payload: int(payload["value"]),
            lambda value: str(value + 1),
            apply,
        )
    )
    gateway.register_descriptor(descriptor)
    try:
        assert gateway._handlers is gateway._registry.handlers
        prepared = gateway._preparers[descriptor.name]({"value": "12"})
        transaction = PostgresConnection.__new__(PostgresConnection)
        assert gateway._handlers[descriptor.name](transaction, prepared) == 2
        assert observed == [(transaction, "13")]
        with pytest.raises(ValueError, match="Duplicate command: test.typed.contract"):
            gateway.register_command(descriptor.name, lambda *_: None)
    finally:
        gateway._handlers.pop(descriptor.name)
        gateway._preparers.pop(descriptor.name)


def test_pure_contract_import_does_not_start_or_import_runtime():
    root = Path(__file__).resolve().parents[2]
    code = """\nimport builtins\noriginal = builtins.__import__\ndef guarded(name, *args, **kwargs):\n    if name in {"app.main", "app.tasks", "app.command_gateway"} or name.startswith(("tempo_storage", "psycopg", "redis", "celery")):\n        raise AssertionError(name)\n    return original(name, *args, **kwargs)\nbuiltins.__import__ = guarded\nfrom app.contracts.command_registry import CommandDescriptor, CommandRegistry\nassert CommandRegistry().handlers == {}\n"""
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "backend")},
        check=True,
    )


def test_descriptor_retains_and_clears_after_commit_callback():
    registry = CommandRegistry()
    events = []
    descriptor = CommandDescriptor(
        "both",
        lambda payload: payload,
        lambda value: value,
        lambda _tx, value: value,
        after_commit=events.append,
    )
    registry.register(descriptor)
    registry.after_commit("both", {"saved": True})
    assert events == [{"saved": True}]
    with pytest.raises(ValueError, match="Duplicate command: both"):
        registry.register(descriptor)
    registry.handlers.pop("both")
    registry.register(
        CommandDescriptor(
            "both",
            lambda payload: payload,
            lambda value: value,
            lambda _tx, value: value,
        )
    )
    registry.after_commit("both", {"saved": False})
    assert events == [{"saved": True}]
