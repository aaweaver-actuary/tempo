"""Transport receipts, nested rollback and durable broker-independent delivery."""

from datetime import datetime, timezone
import hashlib, json, uuid
from tempo_learning_database.adapters import gateway as storage
from tempo_learning_database.adapters.gateway import psycopg
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from tempo_learning_database.connections import connection, current_database
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
        storage.insert_operation_intent(database, operation_id, name, digest, json.dumps(payload))
        row = storage.intent_identity(database, operation_id)
        if tuple(row) != (name, digest):
            raise ReviewConflict(
                "command_identity_reused",
                "Operation ID was already used for another request",
            )


def execute(operation_id, *, background=False, publication=None):
    with connection(background=background) as database:
        storage.lock_operation(database, operation_id)
        row = storage.operation_for_update(database, operation_id)
        if row is None:
            raise HTTPException(404, "Unknown operation")
        if publication is not None and row['command_name'].startswith('publication.') != publication:
            raise HTTPException(403,'Operation requires its owning credential')
        if row["state"] in ("complete", "failed"):
            return
        payload = json.loads(row["payload_json"])
        storage.begin_command_handler(database)
        try:
            if row["command_name"] in (
                "tactics.attempt.submit",
                "defense.attempt.submit",
                "defense.recognition.submit",
            ):
                attempt_id = payload["request"]["attempt_id"]
                digest = request_digest(row["command_name"], payload)
                storage.bind_logical_request(database, row["command_name"], attempt_id, digest)
                logical = storage.logical_request_identity(database, row["command_name"], attempt_id)
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
            storage.rollback_command_handler(database)
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
            storage.fail_operation(database, json.dumps(error_payload), operation_id)
        else:
            storage.release_command_handler(database)
            storage.complete_operation(database, json.dumps(result), operation_id)


def fence_publication(operation_id, name, payload):
    """Terminate one publication identity under execution's exact lock discipline.

    An executing handler holds this advisory lock through effects and receipt
    commit. Intent insertion alone holds no execution authority. A queued worker
    that was selected before this fence must re-read the terminal row in execute.
    The publication boundary validates the request before calling this method.
    """
    if name not in ('publication.activate.v2', 'publication.retire'):
        raise HTTPException(422, 'Unsupported publication fence family')
    fingerprint = request_digest(name, payload)
    with connection() as database:
        storage.lock_operation(database, operation_id)
        storage.insert_operation_intent(database, operation_id, name, fingerprint, json.dumps(payload))
        row = storage.operation_for_update(database, operation_id)
        if (row['command_name'], row['request_hash']) != (name, fingerprint):
            raise ReviewConflict('command_identity_reused', 'Operation ID was already used for another request')
        if row['state'] not in ('complete', 'failed'):
            error = {'status_code': 409, 'code': 'publication_abandoned_before_execution',
                     'detail': 'Publication operation was durably abandoned before execution', 'retryable': False}
            storage.fail_operation(database, json.dumps(error), operation_id)
    return read_operation(operation_id, publication=True)


def read_operation(operation_id, *, publication=None):
    with connection(read_only=True) as database:
        row = storage.read_operation(database, operation_id)
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
