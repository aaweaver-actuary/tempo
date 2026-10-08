"""Typed, idempotent PostgreSQL command boundary used by Celery workers."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid
from typing import Any

import psycopg
from psycopg.errors import DeadlockDetected, LockNotAvailable, SerializationFailure, TransactionTimeout
from fastapi import HTTPException

from . import postgres_store


CommandHandler = Callable[[postgres_store.PostgresConnection, Any], Any]
_handlers: dict[str, CommandHandler] = {}
_preparers: dict[str, Callable[[dict[str, Any]], Any]] = {}
MAX_BACKGROUND_CYCLE_ATTEMPTS = 11
MAX_HTTP_ERROR_DETAIL_BYTES = 16_384


def _is_json_native_detail(detail: Any) -> bool:
    if detail is None or type(detail) in {str, int, float, bool}:
        return True
    if type(detail) is list:
        return all(_is_json_native_detail(item) for item in detail)
    if type(detail) is dict:
        return all(type(key) is str and _is_json_native_detail(value)
                   for key, value in detail.items())
    return False


class CommandConflict(ValueError):
    """An operation ID was reused for a different command or payload."""


def register_command(
    name: str, handler: CommandHandler, *, prepare: Callable[[dict[str, Any]], Any] | None = None,
) -> None:
    """Register publication and optional non-persisted preparation before its transaction."""
    if name in _handlers:
        raise ValueError(f"Duplicate command: {name}")
    _handlers[name] = handler
    if prepare is not None:
        _preparers[name] = prepare


def request_digest(command_name: str, payload: dict[str, Any]) -> str:
    # A paste preview is derived from the current repertoire snapshot. After a
    # successful save that snapshot changes, but replaying the same user save
    # must still resolve to its original receipt.
    if command_name in {"opening_evidence.checkpoint", "cards.review", "cards.review.reconcile"}:
        # Preparation is authoritative derived data; retries bind the original envelope.
        identity_payload = {key: value for key, value in payload.items() if key != "prepared_manifest"}
    elif command_name == "analysis.paste.commit":
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
    expected_retry_cycle: int | None = None,
) -> tuple[bool, dict[str, Any], str | None, int]:
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
            "SELECT command_name,request_hash,state,payload_json,next_retry_at,lease_expires_at,"
            "attempt_count,cycle_attempt_count,retry_cycle "
            "FROM operation_receipts WHERE operation_id=%s FOR UPDATE", (operation_id,),
        ).fetchone()
        if receipt[0] != command_name or receipt[1] != request_hash:
            raise CommandConflict("Operation ID was already used for another request")
        saved_payload = json.loads(receipt[3]) if receipt[3] else payload
        # Terminal delivery must never repopulate a deliberately removed PGN.
        if receipt[2] in {"complete", "failed"}:
            return False, saved_payload, None, receipt[7]
        if receipt[3] is None:
            raw.execute(
                "UPDATE operation_receipts SET payload_json=%s,background=%s WHERE operation_id=%s",
                (json.dumps(saved_payload, separators=(",", ":")), background, operation_id),
            )
        now = datetime.now(timezone.utc)
        if receipt[2] == "blocked" and expected_retry_cycle == receipt[8]:
            raw.execute(
                "UPDATE operation_receipts SET state='queued',retry_cycle=retry_cycle+1,"
                "cycle_attempt_count=0,next_retry_at=NULL,updated_at=NOW() "
                "WHERE operation_id=%s", (operation_id,),
            )
            cycle_attempts = 0
        elif expected_retry_cycle is not None:
            return False, saved_payload, None, receipt[7]
        else:
            cycle_attempts = receipt[7]
        if (receipt[2] in {"complete", "failed", "blocked"} and expected_retry_cycle is None) or (
            receipt[2] == "retrying" and receipt[4] and receipt[4] > now
        ) or (receipt[2] == "executing" and receipt[5] and receipt[5] > now):
            return False, saved_payload, None, cycle_attempts
        if background and cycle_attempts >= MAX_BACKGROUND_CYCLE_ATTEMPTS:
            raw.execute(
                "UPDATE operation_receipts SET state='blocked',attempt_token=NULL,"
                "lease_expires_at=NULL,next_retry_at=NULL,updated_at=NOW() "
                "WHERE operation_id=%s", (operation_id,),
            )
            return False, saved_payload, None, cycle_attempts
        attempt_token = uuid.uuid4().hex
        raw.execute(
            "UPDATE operation_receipts SET state='executing',attempt_count=attempt_count+1,"
            "cycle_attempt_count=cycle_attempt_count+1,attempt_token=%s,"
            "next_retry_at=NULL,lease_expires_at=%s,updated_at=NOW() WHERE operation_id=%s",
            (attempt_token, now + timedelta(minutes=5 if not background else 1), operation_id),
        )
        return True, saved_payload, attempt_token, cycle_attempts + 1


def load_blocked_operation(operation_id: str) -> dict[str, Any] | None:
    with postgres_store.connection(read_only=True) as database:
        row = database.raw.execute(
            "SELECT command_name,payload_json,background,retry_cycle FROM operation_receipts "
            "WHERE operation_id=%s AND state='blocked'", (operation_id,),
        ).fetchone()
    if row is None or row[1] is None:
        return None
    return {"operation_id": operation_id, "command_name": row[0],
            "payload": json.loads(row[1]), "background": row[2], "retry_cycle": row[3]}


def record_operation_retry(
    operation_id: str, attempt_token: str, error: BaseException, *,
    retryable: bool, background: bool,
) -> tuple[bool, float | None]:
    with _writer_connection(background) as database:
        receipt = database.raw.execute(
            "SELECT cycle_attempt_count FROM operation_receipts WHERE operation_id=%s "
            "AND state='executing' AND attempt_token=%s FOR UPDATE",
            (operation_id, attempt_token),
        ).fetchone()
        if receipt is None:
            return True, None
        cycle_attempts = int(receipt[0])
        exhausted = not retryable or (background and cycle_attempts >= MAX_BACKGROUND_CYCLE_ATTEMPTS)
        delay_seconds = None if exhausted else min(30.0, 0.5 * 2 ** (cycle_attempts - 1))
        database.raw.execute(
            "UPDATE operation_receipts SET state=%s,next_retry_at=%s,lease_expires_at=NULL,"
            "attempt_token=NULL,last_error_json=%s,updated_at=NOW() "
            "WHERE operation_id=%s AND state='executing' AND attempt_token=%s",
            ("blocked" if exhausted else "retrying",
             None if exhausted else datetime.now(timezone.utc) + timedelta(seconds=delay_seconds),
             json.dumps({"class": type(error).__name__, "message": str(error)[:500]}),
             operation_id, attempt_token),
        )
        return exhausted, delay_seconds


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
            "lease_expires_at=NULL,attempt_token=NULL,updated_at=NOW() WHERE operation_id=%s", (row[0],),
        )
        return {"operation_id": row[0], "command_name": row[1],
                "payload": json.loads(row[2]), "background": row[3]}


def execute_command(
    operation_id: str, command_name: str, payload: dict[str, Any], *, background: bool = False,
    attempt_token: str | None = None,
) -> Any:
    if command_name not in _handlers:
        raise ValueError(f"Unknown command: {command_name}")
    if not operation_id or len(operation_id) > 128:
        raise ValueError("Operation ID must contain 1 to 128 characters")
    request_hash = request_digest(command_name, payload)
    # The durable receipt owns only the original source envelope. Optional
    # bounded preparation closes its read before the publication transaction.
    prepared = payload
    preparation_error = None
    if command_name in _preparers:
        try:
            prepared = _preparers[command_name](payload)
        except (psycopg.OperationalError, psycopg.InterfaceError,
                DeadlockDetected, LockNotAvailable, SerializationFailure, TransactionTimeout):
            raise
        except Exception as error:
            # Persist definitive preparation failures through the same receipt
            # envelope, after checking delivery identity and the attempt fence.
            preparation_error = error
    command_connection = _writer_connection(background)
    from .services.activity_gate import activity_gate
    write_admission = activity_gate.foreground() if command_name == 'repertoire.prefix_transition.apply' else nullcontext()
    with write_admission, command_connection as database:
        raw = database.raw
        # Serialize two deliveries of one command across worker processes.
        raw.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (operation_id,))
        raw.execute(
            "INSERT INTO operation_receipts(operation_id,command_name,request_hash,state) "
            "VALUES (%s,%s,%s,'pending') ON CONFLICT(operation_id) DO NOTHING",
            (operation_id, command_name, request_hash),
        )
        receipt = raw.execute(
            "SELECT command_name,request_hash,state,response_json,error_json,attempt_token "
            "FROM operation_receipts WHERE operation_id=%s FOR UPDATE",
            (operation_id,),
        ).fetchone()
        if receipt[0] != command_name or receipt[1] != request_hash:
            raise CommandConflict("Operation ID was already used for another request")
        if receipt[2] == "complete":
            return json.loads(receipt[3])
        if receipt[2] == "failed":
            raise RuntimeError(json.loads(receipt[4])["message"])
        if attempt_token is not None and (receipt[2] != "executing" or receipt[5] != attempt_token):
            return None
        raw.execute("SAVEPOINT command_handler")
        try:
            if preparation_error is not None:
                if (command_name == 'repertoire.prefix_transition.apply' and
                        isinstance(preparation_error, HTTPException) and preparation_error.status_code == 503):
                    raise SerializationFailure('Transition preparation yielded to foreground work; retry its durable operation') from preparation_error
                raise preparation_error
            result = _handlers[command_name](database, prepared)
        except (psycopg.OperationalError, psycopg.InterfaceError,
                DeadlockDetected, LockNotAvailable, SerializationFailure, TransactionTimeout):
            # The broker will redeliver; an uncertain commit must not be
            # converted into a permanent failed receipt.
            raise
        except Exception as error:
            raw.execute("ROLLBACK TO SAVEPOINT command_handler")
            if isinstance(error, psycopg.Error) and error.sqlstate == 'P0080':
                error = HTTPException(409, {'code': 'prefix_transition_in_progress', 'message': error.diag.message_primary})
            committed_transition = False
            if command_name == 'repertoire.prefix_transition.apply':
                from .services.prefix_transition_application import reject_unactivated_application
                committed_transition = reject_unactivated_application(database, operation_id, error)
            error_payload = {
                "message": str(error),
                "status_code": error.status_code if isinstance(error, HTTPException) else 500,
            }
            error_payload.update({"code": error.code, "retryable": error.retryable} if hasattr(error, "code") and hasattr(error, "retryable") else {})
            if isinstance(error, HTTPException):
                try:
                    # Preserve message for legacy operation-status callers. New
                    # receipts can also reconstruct the original HTTP detail.
                    if _is_json_native_detail(error.detail) and len(
                        json.dumps(error.detail, allow_nan=False).encode("utf-8")
                    ) <= MAX_HTTP_ERROR_DETAIL_BYTES:
                        error_payload["detail"] = error.detail
                except (TypeError, ValueError, RecursionError):
                    # Unsupported/cyclic detail retains the existing envelope.
                    pass
            error_json = json.dumps(error_payload)
            if committed_transition:
                raw.execute("UPDATE operation_receipts SET state='blocked',last_error_json=%s,attempt_token=NULL,lease_expires_at=NULL,updated_at=NOW() WHERE operation_id=%s", (error_json, operation_id))
                return None
            raw.execute(
                "UPDATE operation_receipts SET state='failed',error_json=%s,"
                "attempt_token=NULL,lease_expires_at=NULL,updated_at=NOW() "
                "WHERE operation_id=%s",
                (error_json, operation_id),
            )
            return None
        raw.execute("RELEASE SAVEPOINT command_handler")
        if command_name == 'repertoire.prefix_transition.apply' and result.get('status') == 'pending':
            raw.execute("UPDATE operation_receipts SET state='pending',attempt_token=NULL,lease_expires_at=NULL,next_retry_at=NULL,updated_at=NOW() WHERE operation_id=%s", (operation_id,))
            return result
        response_json = json.dumps(result, sort_keys=True, separators=(",", ":"))
        raw.execute(
            "UPDATE operation_receipts SET state='complete',response_json=%s,"
            "attempt_token=NULL,lease_expires_at=NULL,updated_at=NOW() "
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
            "next_retry_at,last_error_json,lease_expires_at,payload_json,retry_cycle,cycle_attempt_count "
            "FROM operation_receipts WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()
        transition_progress = None
        if receipt and receipt[0] == 'repertoire.prefix_transition.apply':
            progress = database.execute_native('SELECT state,graph_generation,staging_task_id,graph_task_id,integrity_task_id,integrity_generation,queue_task_id,queue_generation,queue_date,last_error FROM prefix_transition_applications WHERE operation_id=%s', (operation_id,)).fetchone()
            transition_progress = dict(progress) if progress else None
    if receipt is None:
        return {"operation_id": operation_id, "state": "unknown",
                "message": "No durable receipt exists yet. Retry with the same Idempotency-Key."}
    if (command_name is not None and receipt[0] != command_name) or (
        request_hash is not None and receipt[1] != request_hash
    ):
        raise CommandConflict("Operation ID was already used for another request")
    state, response_json, error_json = receipt[2], receipt[3], receipt[4]
    response: dict[str, Any] = {"operation_id": operation_id, "state": state}
    if transition_progress is not None:
        response["transition"] = transition_progress
        response["transition"]["recovery"] = (
            "Retry this operation through its existing retry endpoint to resume the linked tasks; the activated graph remains committed."
            if transition_progress['state'] == 'recovery_required' else
            "Approve a fresh plan with a new operation identity; activation did not occur."
            if transition_progress['state'] == 'rejected' else None)
    response["attempt_count"] = int(receipt[5])
    response["retry_cycle"] = int(receipt[10])
    response["cycle_attempt_count"] = int(receipt[11])
    if receipt[6] is not None:
        response["next_retry_at"] = receipt[6].isoformat()
    if receipt[7] is not None:
        response["last_error"] = json.loads(receipt[7])
    if state == "blocked":
        response["message"] = "Automatic retries stopped. Retry this operation with the same Idempotency-Key after checking the reported error."
    if state == "pending" and receipt[9] is None:
        response["message"] = ("Legacy receipt has no saved payload. Recover only from matching "
                               "journal or outbox evidence; automatic replay is unavailable.")
    if response_json is not None:
        response["response"] = json.loads(response_json)
    if error_json is not None:
        response["error"] = json.loads(error_json)
    return response
