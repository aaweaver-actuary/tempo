"""Typed, idempotent PostgreSQL command boundary used by Celery workers."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
from typing import Any

import psycopg
from psycopg.errors import DeadlockDetected, LockNotAvailable, SerializationFailure, TransactionTimeout
from fastapi import HTTPException

from . import postgres_store


CommandHandler = Callable[[postgres_store.PostgresConnection, dict[str, Any]], Any]
_handlers: dict[str, CommandHandler] = {}


class CommandConflict(ValueError):
    """An operation ID was reused for a different command or payload."""


def register_command(name: str, handler: CommandHandler) -> None:
    if name in _handlers:
        raise ValueError(f"Duplicate command: {name}")
    _handlers[name] = handler


def request_digest(command_name: str, payload: dict[str, Any]) -> str:
    # A paste preview is derived from the current repertoire snapshot. After a
    # successful save that snapshot changes, but replaying the same user save
    # must still resolve to its original receipt.
    identity_payload = payload["request"] if command_name == "analysis.paste.commit" else payload
    serialized = json.dumps([command_name, identity_payload], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def execute_command(
    operation_id: str, command_name: str, payload: dict[str, Any], *, background: bool = False,
) -> Any:
    if command_name not in _handlers:
        raise ValueError(f"Unknown command: {command_name}")
    if not operation_id or len(operation_id) > 128:
        raise ValueError("Operation ID must contain 1 to 128 characters")
    request_hash = request_digest(command_name, payload)
    if background:
        from .database import background_connection
        command_connection = background_connection()
    else:
        command_connection = postgres_store.connection(read_only=False)
    with command_connection as database:
        raw = database.raw
        # Serialize two deliveries of one command across worker processes.
        raw.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (operation_id,))
        raw.execute(
            "INSERT INTO operation_receipts(operation_id,command_name,request_hash,state) "
            "VALUES (%s,%s,%s,'pending') ON CONFLICT(operation_id) DO NOTHING",
            (operation_id, command_name, request_hash),
        )
        receipt = raw.execute(
            "SELECT command_name,request_hash,state,response_json,error_json "
            "FROM operation_receipts WHERE operation_id=%s FOR UPDATE",
            (operation_id,),
        ).fetchone()
        if receipt[0] != command_name or receipt[1] != request_hash:
            raise CommandConflict("Operation ID was already used for another request")
        if receipt[2] == "complete":
            return json.loads(receipt[3])
        if receipt[2] == "failed":
            raise RuntimeError(json.loads(receipt[4])["message"])
        raw.execute("SAVEPOINT command_handler")
        try:
            result = _handlers[command_name](database, payload)
        except (psycopg.OperationalError, psycopg.InterfaceError,
                DeadlockDetected, LockNotAvailable, SerializationFailure, TransactionTimeout):
            # The broker will redeliver; an uncertain commit must not be
            # converted into a permanent failed receipt.
            raise
        except Exception as error:
            raw.execute("ROLLBACK TO SAVEPOINT command_handler")
            error_json = json.dumps({
                "message": str(error),
                "status_code": error.status_code if isinstance(error, HTTPException) else 500,
            })
            raw.execute(
                "UPDATE operation_receipts SET state='failed',error_json=%s,updated_at=NOW() "
                "WHERE operation_id=%s",
                (error_json, operation_id),
            )
            return None
        raw.execute("RELEASE SAVEPOINT command_handler")
        response_json = json.dumps(result, sort_keys=True, separators=(",", ":"))
        raw.execute(
            "UPDATE operation_receipts SET state='complete',response_json=%s,updated_at=NOW() "
            "WHERE operation_id=%s",
            (response_json, operation_id),
        )
        return result


def read_operation(
    operation_id: str, *, command_name: str | None = None, request_hash: str | None = None,
    background: bool = False,
) -> dict[str, Any]:
    if background:
        from .database import background_read_connection
        receipt_connection = background_read_connection()
    else:
        receipt_connection = postgres_store.connection(read_only=True)
    with receipt_connection as database:
        receipt = database.raw.execute(
            "SELECT command_name,request_hash,state,response_json,error_json "
            "FROM operation_receipts WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()
    if receipt is None:
        return {"operation_id": operation_id, "state": "pending"}
    if (command_name is not None and receipt[0] != command_name) or (
        request_hash is not None and receipt[1] != request_hash
    ):
        raise CommandConflict("Operation ID was already used for another request")
    state, response_json, error_json = receipt[2], receipt[3], receipt[4]
    response: dict[str, Any] = {"operation_id": operation_id, "state": state}
    if response_json is not None:
        response["response"] = json.loads(response_json)
    if error_json is not None:
        response["error"] = json.loads(error_json)
    return response
