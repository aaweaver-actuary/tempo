"""Typed, idempotent PostgreSQL command boundary used by Celery workers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
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
    if command_name == "analysis.paste.commit":
        identity_payload = payload["request"]
    elif command_name == "discovery.accept":
        # Recommendation preparation can change during a retry. The accepted
        # choice and evidence revision identify the user's operation.
        identity_payload = {key: payload[key] for key in (
            "opportunity_id", "selected_move_uci", "evidence_fingerprint"
        )}
    else:
        identity_payload = payload
    serialized = json.dumps([command_name, identity_payload], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _writer_connection(background: bool):
    if background:
        from .database import background_connection
        return background_connection()
    return postgres_store.connection(read_only=False)


def record_operation_attempt(
    operation_id: str, command_name: str, payload: dict[str, Any], *, background: bool,
    allow_blocked: bool = False,
) -> tuple[bool, dict[str, Any]]:
    """Persist retry identity before work; a crash can then be recovered."""
    request_hash = request_digest(command_name, payload)
    with _writer_connection(background) as database:
        raw = database.raw
        raw.execute(
            "INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,payload_json,background) "
            "VALUES(%s,%s,%s,'queued',%s,%s) ON CONFLICT(operation_id) DO NOTHING",
            (operation_id, command_name, request_hash,
             json.dumps(payload, separators=(",", ":")), background),
        )
        receipt = raw.execute(
            "SELECT command_name,request_hash,state,payload_json,next_retry_at,lease_expires_at "
            "FROM operation_receipts WHERE operation_id=%s FOR UPDATE", (operation_id,),
        ).fetchone()
        if receipt[0] != command_name or receipt[1] != request_hash:
            raise CommandConflict("Operation ID was already used for another request")
        saved_payload = json.loads(receipt[3]) if receipt[3] else payload
        if receipt[3] is None:
            raw.execute(
                "UPDATE operation_receipts SET payload_json=%s,background=%s WHERE operation_id=%s",
                (json.dumps(saved_payload, separators=(",", ":")), background, operation_id),
            )
        now = datetime.now(timezone.utc)
        if receipt[2] in {"complete", "failed"} or (receipt[2] == "blocked" and not allow_blocked) or (
            receipt[2] == "retrying" and receipt[4] and receipt[4] > now
        ) or (receipt[2] == "executing" and receipt[5] and receipt[5] > now):
            return False, saved_payload
        raw.execute(
            "UPDATE operation_receipts SET state='executing',attempt_count=attempt_count+1,"
            "next_retry_at=NULL,lease_expires_at=%s,updated_at=NOW() WHERE operation_id=%s",
            (now + timedelta(minutes=5 if not background else 1), operation_id),
        )
        return True, saved_payload


def load_blocked_operation(operation_id: str) -> dict[str, Any] | None:
    with postgres_store.connection(read_only=True) as database:
        row = database.raw.execute(
            "SELECT command_name,payload_json,background FROM operation_receipts "
            "WHERE operation_id=%s AND state='blocked'", (operation_id,),
        ).fetchone()
    if row is None or row[1] is None:
        return None
    return {"operation_id": operation_id, "command_name": row[0],
            "payload": json.loads(row[1]), "background": row[2]}


def record_operation_retry(
    operation_id: str, error: BaseException, *, delay_seconds: float,
    exhausted: bool, background: bool,
) -> None:
    with _writer_connection(background) as database:
        database.raw.execute(
            "UPDATE operation_receipts SET state=%s,next_retry_at=%s,lease_expires_at=NULL,"
            "last_error_json=%s,updated_at=NOW() "
            "WHERE operation_id=%s AND state NOT IN ('complete','failed')",
            ("blocked" if exhausted else "retrying",
             None if exhausted else datetime.now(timezone.utc) + timedelta(seconds=delay_seconds),
             json.dumps({"class": type(error).__name__, "message": str(error)[:500]}),
             operation_id),
        )


def claim_recoverable_operation() -> dict[str, Any] | None:
    """Reserve one abandoned operation for broker redelivery."""
    with _writer_connection(True) as database:
        row = database.raw.execute(
            "SELECT operation_id,command_name,payload_json,background FROM operation_receipts "
            "WHERE payload_json IS NOT NULL AND ("
            "(state='retrying' AND next_retry_at<=NOW()) OR "
            "(state='executing' AND lease_expires_at<=NOW()) OR "
            "(state='queued' AND updated_at<=NOW()-INTERVAL '30 seconds')) "
            "ORDER BY updated_at,operation_id LIMIT 1 FOR UPDATE SKIP LOCKED",
        ).fetchone()
        if row is None:
            return None
        database.raw.execute(
            "UPDATE operation_receipts SET state='queued',next_retry_at=NOW()+INTERVAL '30 seconds',"
            "lease_expires_at=NULL,updated_at=NOW() WHERE operation_id=%s", (row[0],),
        )
        return {"operation_id": row[0], "command_name": row[1],
                "payload": json.loads(row[2]), "background": row[3]}


def execute_command(
    operation_id: str, command_name: str, payload: dict[str, Any], *, background: bool = False,
) -> Any:
    if command_name not in _handlers:
        raise ValueError(f"Unknown command: {command_name}")
    if not operation_id or len(operation_id) > 128:
        raise ValueError("Operation ID must contain 1 to 128 characters")
    request_hash = request_digest(command_name, payload)
    command_connection = _writer_connection(background)
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
            "SELECT command_name,request_hash,state,response_json,error_json,attempt_count,"
            "next_retry_at,last_error_json,lease_expires_at "
            "FROM operation_receipts WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()
    if receipt is None:
        return {"operation_id": operation_id, "state": "unknown",
                "message": "No durable receipt exists yet. Retry with the same Idempotency-Key."}
    if (command_name is not None and receipt[0] != command_name) or (
        request_hash is not None and receipt[1] != request_hash
    ):
        raise CommandConflict("Operation ID was already used for another request")
    state, response_json, error_json = receipt[2], receipt[3], receipt[4]
    response: dict[str, Any] = {"operation_id": operation_id, "state": state}
    response["attempt_count"] = int(receipt[5])
    if receipt[6] is not None:
        response["next_retry_at"] = receipt[6].isoformat()
    if receipt[7] is not None:
        response["last_error"] = json.loads(receipt[7])
    if state == "blocked":
        response["message"] = "Automatic retries stopped. Retry this operation with the same Idempotency-Key after checking the reported error."
    if response_json is not None:
        response["response"] = json.loads(response_json)
    if error_json is not None:
        response["error"] = json.loads(error_json)
    return response
