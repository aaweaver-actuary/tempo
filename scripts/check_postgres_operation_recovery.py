"""Verify durable operation retry and replay against disposable PostgreSQL."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import postgres_store
from app.command_gateway import (
    CommandConflict, claim_recoverable_operation, execute_command, read_operation,
    record_operation_attempt, record_operation_retry, register_command,
)


def main() -> None:
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("This check requires the disposable PostgreSQL test instance")
    os.environ["TEMPO_DATABASE_WRITE_URL"] = "postgresql://postgres@postgres:5432/tempo"
    os.environ["TEMPO_DATABASE_READ_URL"] = os.environ["TEMPO_DATABASE_WRITE_URL"]
    executed: list[str] = []

    def handler(_database, payload):
        executed.append(payload["value"])
        return {"value": payload["value"]}

    register_command("test.recover", handler)
    operation_id = f"recovery-{uuid.uuid4().hex}"
    payload = {"value": "saved"}
    should_execute, saved_payload = record_operation_attempt(
        operation_id, "test.recover", payload, background=True,
    )
    assert should_execute and saved_payload == payload
    assert read_operation(operation_id)["state"] == "executing"
    assert record_operation_attempt(operation_id, "test.recover", payload,
                                    background=True)[0] is False
    record_operation_retry(operation_id, RuntimeError("injected timeout"),
                           delay_seconds=0, exhausted=False, background=True)
    assert read_operation(operation_id)["state"] == "retrying"
    recovered = claim_recoverable_operation()
    assert recovered == {"operation_id": operation_id, "command_name": "test.recover",
                         "payload": payload, "background": True}
    postgres_store.close_pools()  # A restarted worker discovers the saved command.
    assert record_operation_attempt(operation_id, "test.recover", payload,
                                    background=True)[0] is True
    assert execute_command(operation_id, "test.recover", payload, background=True) == payload
    assert read_operation(operation_id)["state"] == "complete"
    assert execute_command(operation_id, "test.recover", payload, background=True) == payload
    assert executed == ["saved"]
    try:
        record_operation_attempt(operation_id, "test.recover", {"value": "changed"},
                                 background=True)
    except CommandConflict:
        pass
    else:
        raise AssertionError("A changed payload reused the operation identity")
    blocked_id = f"blocked-{uuid.uuid4().hex}"
    record_operation_attempt(blocked_id, "test.recover", payload, background=True)
    record_operation_retry(blocked_id, RuntimeError("retry budget exhausted"),
                           delay_seconds=0, exhausted=True, background=True)
    assert read_operation(blocked_id)["state"] == "blocked"
    assert record_operation_attempt(blocked_id, "test.recover", payload,
                                    background=True)[0] is False
    assert record_operation_attempt(blocked_id, "test.recover", payload,
                                    background=True, allow_blocked=True)[0] is True
    assert execute_command(blocked_id, "test.recover", payload, background=True) == payload
    assert read_operation(blocked_id)["state"] == "complete"
    postgres_store.close_pools()
    print("PASS durable retry, restart replay, payload conflict, and explicit blocked recovery")


if __name__ == "__main__":
    main()
