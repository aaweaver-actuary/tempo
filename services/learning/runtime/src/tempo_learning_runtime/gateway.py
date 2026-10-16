"""Transport receipts, nested rollback and durable broker-independent delivery."""

from datetime import datetime, timezone
import hashlib, json, uuid
import psycopg
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from .database import connection, current_database
from .review_conflicts import ReviewConflict

_handlers = {}


def register_command(name, handler, **unused):
    if name in _handlers:
        raise ValueError("Duplicate Learning command: " + name)
    _handlers[name] = handler


def request_digest(name, payload):
    identity = {
        key: value for key, value in payload.items() if key != "prepared_manifest"
    }
    return hashlib.sha256(
        json.dumps([name, identity], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def record_intent(operation_id, name, payload):
    if name not in _handlers:
        raise HTTPException(422, "Unknown Learning command")
    if not operation_id or len(operation_id) > 128:
        raise HTTPException(422, "Idempotency-Key must contain 1 to 128 characters")
    digest = request_digest(name, payload)
    with connection() as database:
        database.execute_native(
            "INSERT INTO operation_receipts(operation_id,command_name,request_hash,payload_json) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (operation_id, name, digest, json.dumps(payload)),
        )
        row = database.execute_native(
            "SELECT command_name,request_hash FROM operation_receipts WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()
        if tuple(row) != (name, digest):
            raise ReviewConflict(
                "command_identity_reused",
                "Operation ID was already used for another request",
            )


def execute(operation_id, *, background=False, publication=None):
    with connection(background=background) as database:
        database.execute_native(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (operation_id,)
        )
        row = database.execute_native(
            "SELECT * FROM operation_receipts WHERE operation_id=%s FOR UPDATE",
            (operation_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Unknown operation")
        if publication is not None and row['command_name'].startswith('publication.') != publication:
            raise HTTPException(403,'Operation requires its owning credential')
        if row["state"] in ("complete", "failed"):
            return
        payload = json.loads(row["payload_json"])
        database.execute_native("SAVEPOINT command_handler")
        try:
            if row["command_name"] in (
                "tactics.attempt.submit",
                "defense.attempt.submit",
                "defense.recognition.submit",
            ):
                attempt_id = payload["request"]["attempt_id"]
                digest = request_digest(row["command_name"], payload)
                database.execute_native(
                    "INSERT INTO logical_request_bindings VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                    (row["command_name"], attempt_id, digest),
                )
                logical = database.execute_native(
                    "SELECT request_hash FROM logical_request_bindings WHERE family=%s AND attempt_id=%s FOR UPDATE",
                    (row["command_name"], attempt_id),
                ).fetchone()
                if logical[0] != digest:
                    raise ReviewConflict(
                        "review_attempt_reused",
                        "Attempt ID was already used with another response",
                    )
            result = _handlers[row["command_name"]](database, payload)
        except (
            psycopg.OperationalError,
            psycopg.InterfaceError,
            psycopg.errors.SerializationFailure,
            psycopg.errors.DeadlockDetected,
            psycopg.errors.LockNotAvailable,
            psycopg.errors.QueryCanceled,
        ):
            raise
        except (HTTPException, ValueError, KeyError) as error:
            database.execute_native("ROLLBACK TO SAVEPOINT command_handler")
            error_payload = {
                "status_code": error.status_code
                if isinstance(error, HTTPException)
                else 409,
                "detail": error.detail
                if isinstance(error, HTTPException)
                else str(error),
                "message": str(error),
            }
            if isinstance(error, ReviewConflict):
                error_payload.update(code=error.code, retryable=error.retryable)
            database.execute_native(
                "UPDATE operation_receipts SET state='failed',error_json=%s,updated_at=NOW() WHERE operation_id=%s",
                (json.dumps(error_payload), operation_id),
            )
        else:
            database.execute_native("RELEASE SAVEPOINT command_handler")
            database.execute_native(
                "UPDATE operation_receipts SET state='complete',response_json=%s,updated_at=NOW() WHERE operation_id=%s",
                (json.dumps(result), operation_id),
            )


def read_operation(operation_id, *, publication=None):
    with connection(read_only=True) as database:
        row = database.execute_native(
            "SELECT * FROM operation_receipts WHERE operation_id=%s", (operation_id,)
        ).fetchone()
    if row is None:
        return {"operation_id": operation_id, "state": "unknown"}
    if publication is not None and row['command_name'].startswith('publication.') != publication:
        raise HTTPException(403,'Operation requires its owning credential')
    return {
        "operation_id": operation_id,
        "state": row["state"],
        **({"workspace_id": current_database.get().configuration.workspace_id,
            "command_name": row["command_name"], "request": json.loads(row["payload_json"])}
           if publication is True else {}),
        **(
            {"response": json.loads(row["response_json"])}
            if row["response_json"]
            else {}
        ),
        **({"error": json.loads(row["error_json"])} if row["error_json"] else {}),
    }


def operation_result(operation_id):
    receipt = read_operation(operation_id)
    if receipt["state"] == "complete":
        return receipt["response"]
    if receipt["state"] == "failed":
        error = receipt["error"]
        if error.get("code"):
            raise ReviewConflict(
                error["code"], error["detail"], retryable=error.get("retryable", False)
            )
        raise HTTPException(error["status_code"], error["detail"])
    return JSONResponse(
        status_code=202,
        content={"operation_id": operation_id, "state": receipt["state"]},
        headers={"Location": "/api/operations/" + operation_id},
    )


def dispatch(name, payload, idempotency_key=None):
    operation_id = idempotency_key or uuid.uuid4().hex
    record_intent(operation_id, name, payload)
    execute(operation_id)
    return operation_result(operation_id)
