"""HTTP-facing command dispatch with an explicit pending-operation contract."""

from __future__ import annotations

import uuid
import time
from typing import Any

from celery.exceptions import TimeoutError as CeleryTimeout
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from kombu.exceptions import OperationalError as BrokerUnavailable
from redis import RedisError

from .celery_app import celery_app
from .command_gateway import CommandConflict, read_operation, request_digest


def dispatch_command(
    command_name: str,
    payload: dict[str, Any],
    *,
    idempotency_key: str | None,
    wait_seconds: float = 2.0,
    background: bool = False,
) -> Any | JSONResponse:
    operation_id = idempotency_key or uuid.uuid4().hex
    if len(operation_id) > 128:
        raise HTTPException(422, "Idempotency-Key must be at most 128 characters")
    try:
        task = celery_app.send_task(
            "app.tasks.execute_background_command" if background else
            "app.tasks.execute_foreground_command",
            args=[operation_id, command_name, payload],
            task_id=operation_id,
            queue="background" if background else "foreground",
            headers={"submitted_at": time.time()},
        )
    except BrokerUnavailable as error:
        raise HTTPException(503, "Save queue unavailable; retry with the same Idempotency-Key") from error
    try:
        task.get(timeout=wait_seconds, propagate=False)
    except (CeleryTimeout, RedisError, BrokerUnavailable):
        # Publishing may have succeeded even when the result backend is down.
        # The durable PostgreSQL receipt remains the source of truth.
        pass
    try:
        receipt = read_operation(
            operation_id,
            command_name=command_name,
            request_hash=request_digest(command_name, payload),
            background=background,
        )
    except CommandConflict as error:
        if command_name in {"cards.review", "cards.review.reconcile"}:
            from .review_conflicts import ReviewConflict
            raise ReviewConflict("command_identity_reused", str(error)) from error
        raise HTTPException(409, str(error)) from error
    if receipt["state"] == "complete":
        return receipt["response"]
    if receipt["state"] == "failed":
        error = receipt["error"]
        if error.get("code") and error.get("status_code") == 409:
            from .review_conflicts import ReviewConflict
            raise ReviewConflict(error["code"], error.get("detail", error["message"]),
                                 retryable=error.get("retryable", False))
        raise HTTPException(error.get("status_code", 500), error.get("detail", error.get("message", "Save failed")))
    return JSONResponse(
        status_code=202,
        content={"operation_id": operation_id, "state": receipt["state"],
                 **({"message": receipt["message"]} if receipt.get("message") else {})},
        headers={"Location": f"/api/operations/{operation_id}"},
    )
