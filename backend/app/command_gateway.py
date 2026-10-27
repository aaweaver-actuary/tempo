"""Typed, idempotent PostgreSQL command boundary used by Celery workers."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid
from typing import Any

from tempo_legacy_database.adapters.commands.receipts import storage
from tempo_legacy_database.adapters.commands.receipts.storage import (
    psycopg, DeadlockDetected, LockNotAvailable, SerializationFailure, TransactionTimeout,
    _writer_connection,
)
from fastapi import HTTPException

from tempo_legacy_database import interfaces as postgres_store
from .services.redis_admission_gate import BackgroundAdmissionDeferred


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


def record_operation_attempt(
    operation_id: str, command_name: str, payload: dict[str, Any], *, background: bool,
    expected_retry_cycle: int | None = None,
) -> tuple[bool, dict[str, Any], str | None, int]:
    """Persist retry identity before work; a crash can then be recovered."""
    request_hash = request_digest(command_name, payload)
    with _writer_connection(background) as database:
        raw = storage.receipt_writer(database)
        storage.insert_queued(raw, operation_id, command_name, request_hash, payload, background)
        receipt = storage.locked_attempt(raw, operation_id)
        if receipt[0] != command_name or receipt[1] != request_hash:
            raise CommandConflict("Operation ID was already used for another request")
        saved_payload = json.loads(receipt[3]) if receipt[3] else payload
        # Terminal delivery must never repopulate a deliberately removed PGN.
        if receipt[2] in {"complete", "failed"}:
            return False, saved_payload, None, receipt[7]
        if receipt[3] is None:
            storage.save_missing_payload(raw, saved_payload, background, operation_id)
        now = datetime.now(timezone.utc)
        if receipt[2] == "blocked" and expected_retry_cycle == receipt[8]:
            storage.restart_blocked(raw, operation_id)
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
            storage.block_exhausted(raw, operation_id)
            return False, saved_payload, None, cycle_attempts
        attempt_token = uuid.uuid4().hex
        storage.start_attempt(raw, attempt_token, lambda: now + timedelta(minutes=5 if not background else 1), operation_id)
        return True, saved_payload, attempt_token, cycle_attempts + 1


def load_blocked_operation(operation_id: str) -> dict[str, Any] | None:
    with storage.foreground_receipt_reader() as database:
        row = storage.blocked_operation(database, operation_id)
    if row is None or row[1] is None:
        return None
    return {"operation_id": operation_id, "command_name": row[0],
            "payload": json.loads(row[1]), "background": row[2], "retry_cycle": row[3]}


def record_operation_retry(
    operation_id: str, attempt_token: str, error: BaseException, *,
    retryable: bool, background: bool,
) -> tuple[bool, float | None]:
    with _writer_connection(background) as database:
        receipt = storage.locked_retry(database, operation_id, attempt_token)
        if receipt is None:
            return True, None
        cycle_attempts = int(receipt[0])
        exhausted = not retryable or (background and cycle_attempts >= MAX_BACKGROUND_CYCLE_ATTEMPTS)
        delay_seconds = None if exhausted else min(30.0, 0.5 * 2 ** (cycle_attempts - 1))
        storage.save_retry(database, "blocked" if exhausted else "retrying", lambda: None if exhausted else datetime.now(timezone.utc) + timedelta(seconds=delay_seconds), lambda: json.dumps({"class": type(error).__name__, "message": str(error)[:500]}), operation_id, attempt_token)
        return exhausted, delay_seconds


def defer_operation_for_foreground(operation_id: str, attempt_token: str) -> bool:
    """A fenced admission denial is waiting, not a failed command attempt."""
    from .services.activity_gate import activity_gate
    with activity_gate.background_control(), _writer_connection(True) as database:
        return storage.defer_for_foreground(database, operation_id, attempt_token)


def claim_recoverable_operation(*, allowed_background_commands=None, paused_commands=()) -> dict[str, Any] | None:
    """Reserve one abandoned operation for broker redelivery."""
    with _writer_connection(True) as database:
        row = storage.recoverable_receipt(database, allowed_background_commands, paused_commands)
        if row is None:
            return None
        storage.reserve_recovery(database, row[0])
        return {"operation_id": row[0], "command_name": row[1],
                "payload": json.loads(row[2]), "background": row[3]}


def execute_command(
    operation_id: str, command_name: str, payload: dict[str, Any], *, background: bool = False,
    attempt_token: str | None = None,
) -> Any:
    from .services.queue_refresh_wakeup import capture_queue_refresh_request, wake_queue_refresh

    with capture_queue_refresh_request() as queue_request:
        result = _execute_command(operation_id, command_name, payload,
                                  background=background, attempt_token=attempt_token)
    if result is not None and command_name == 'settings.update':
        from .services.study_priority import refresh_policy
        refresh_policy()  # Post-commit generation fences any older policy read.
    # The inner boundary commits and closes before returning. Failed handlers
    # return None after rollback; their transient enqueue must never emit a wake.
    if result is not None and (queue_request.requested or
            (command_name == "queue.ensure_current" and result.get("refresh_pending"))):
        from .services.background_runtime import training_timing_stage
        with training_timing_stage("post_commit_wake"):
            wake_queue_refresh()
    return result


def _execute_command(
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
        except (*storage.TRANSIENT_DATABASE_ERRORS, BackgroundAdmissionDeferred):
            raise
        except Exception as error:
            from .prefix_evaluation_api import PrefixEvaluationForegroundDeferred
            if isinstance(error, PrefixEvaluationForegroundDeferred):
                raise BackgroundAdmissionDeferred('Waiting for foreground activity') from error
            # Persist definitive preparation failures through the same receipt
            # envelope, after checking delivery identity and the attempt fence.
            preparation_error = error
    command_connection = _writer_connection(background)
    from .services.activity_gate import activity_gate
    write_admission = activity_gate.foreground() if command_name == 'repertoire.prefix_transition.apply' else nullcontext()
    with write_admission, command_connection as database:
        raw = storage.receipt_writer(database)
        # Serialize two deliveries of one command across worker processes.
        storage.lock_operation(raw, operation_id)
        storage.insert_pending(raw, operation_id, command_name, request_hash)
        receipt = storage.locked_publication(raw, operation_id)
        if receipt[0] != command_name or receipt[1] != request_hash:
            raise CommandConflict("Operation ID was already used for another request")
        if receipt[2] == "complete":
            return json.loads(receipt[3])
        if receipt[2] == "failed":
            raise RuntimeError(json.loads(receipt[4])["message"])
        if attempt_token is not None and (receipt[2] != "executing" or receipt[5] != attempt_token):
            return None
        storage.begin_handler(raw)
        try:
            if preparation_error is not None:
                if (command_name == 'repertoire.prefix_transition.apply' and
                        isinstance(preparation_error, HTTPException) and preparation_error.status_code == 503):
                    raise SerializationFailure('Transition preparation yielded to foreground work; retry its durable operation') from preparation_error
                raise preparation_error
            result = _handlers[command_name](database, prepared)
        except (*storage.TRANSIENT_DATABASE_ERRORS, BackgroundAdmissionDeferred):
            # The broker will redeliver; an uncertain commit must not be
            # converted into a permanent failed receipt.
            raise
        except Exception as error:
            storage.rollback_handler(raw)
            if storage.is_transition_in_progress(error):
                error = HTTPException(409, {'code': 'prefix_transition_in_progress', 'message': storage.primary_error_message(error)})
            committed_transition = False
            if command_name == 'repertoire.prefix_transition.apply':
                from .services.prefix_transition_application import reject_unactivated_application
                committed_transition = reject_unactivated_application(database, operation_id, error)
            error_payload = {
                "message": str(error),
                "status_code": error.status_code if isinstance(error, HTTPException) else 500,
            }
            if storage.is_deleted_card_content(error):
                error_payload.update(status_code=409, message="This card was permanently deleted. Refresh the workspace before retrying.")
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
                storage.block_transition(raw, error_json, operation_id)
                return None
            storage.fail_operation(raw, error_json, operation_id)
            return None
        storage.release_handler(raw)
        if command_name == 'repertoire.prefix_transition.apply' and result.get('status') == 'pending':
            storage.keep_transition_pending(raw, operation_id)
            return result
        response_json = json.dumps(result, sort_keys=True, separators=(",", ":"))
        storage.complete_operation(raw, response_json, operation_id)
        return result


def read_operation(
    operation_id: str, *, command_name: str | None = None, request_hash: str | None = None,
    background: bool = False,
    position_claim_request_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if background:
        from tempo_legacy_database.connections import background_read_connection
        from .services.activity_gate import activity_gate
        receipt_connection = background_read_connection()
        receipt_control = activity_gate.background_control()
    else:
        receipt_connection = storage.foreground_receipt_reader()
        receipt_control = nullcontext()
    with receipt_control, receipt_connection as database:
        receipt = storage.operation_status(database, operation_id)
        transition_progress = None
        if receipt and receipt[0] == 'repertoire.prefix_transition.apply':
            progress = storage.transition_progress(database, operation_id)
            transition_progress = dict(progress) if progress else None
    if receipt is None:
        return {"operation_id": operation_id, "state": "unknown",
                "message": "No durable receipt exists yet. Retry with the same Idempotency-Key."}
    if (command_name is not None and receipt[0] != command_name) or (
        request_hash is not None and receipt[1] != request_hash
    ):
        raise CommandConflict("Operation ID was already used for another request")
    if (position_claim_request_payload is not None and receipt[0] == "games.analysis.position.claim"
            and receipt[9] is not None):
        saved_payload = json.loads(receipt[9])
        # Historical plans did not retain the hint; preserve their recovery.
        # New plans bind the caller's request using the original canonical hash,
        # without preparing another position or changing the saved generation.
        if "claim_request" in saved_payload:
            replay_payload = {**saved_payload, "claim_request": position_claim_request_payload}
            if request_digest(receipt[0], replay_payload) != receipt[1]:
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
