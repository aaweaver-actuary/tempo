"""HTTP transport for the retained manual prefix split commands."""

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from typing import Literal

from tempo_storage.connections import read_connection
from tempo_storage.foreground_controls import prefix_splits as storage
from tempo_storage.study import deferred_prefix_splits as deferred_storage
from tempo_storage.commands.receipts import storage as receipt_storage
import json

from .contracts.transport.content import PrefixSplitRequest, PrefixSplitResponse
from .services.prefix_split import preview_prefix_split

router = APIRouter()


class DeferredPrefixSplitResponse(BaseModel):
    status: Literal["waiting_review", "ready", "held"]
    intent_id: str
    card_id: str
    source_revision: int
    queue_entry_id: int
    reason: str | None = None


@router.get("/api/cards/{identifier}/prefix-split/intents/{intent_id}")
def prefix_split_intent_status(identifier: str, intent_id: str):
    with read_connection() as database:
        intent = deferred_storage.intent(database, intent_id)
        child = (receipt_storage.foreground_operation_state(database, intent["child_operation_id"])
                 if intent is not None and intent["state"] == "ready" and intent["child_operation_id"] else None)
    if intent is None or intent["card_id"] != identifier:
        raise HTTPException(404, "Split intent not found")
    reason = intent["reason"]
    if child is not None and child["last_error_json"]:
        try:
            last_error = json.loads(child["last_error_json"])
            if last_error.get("code") == "prerequisite_pending":
                reason = last_error.get("message")
        except (TypeError, ValueError, AttributeError):
            pass
    return {"status": intent["state"], "intent_id": intent["intent_id"], "card_id": intent["card_id"],
            "source_revision": intent["source_revision"], "queue_entry_id": intent["queue_entry_id"],
            "reason": reason, "result": intent["result_json"]}


@router.get("/api/cards/{identifier}/prefix-split", response_model=PrefixSplitResponse)
def prefix_split_preview(identifier: str):
    with read_connection() as database:
        try:
            result = preview_prefix_split(database, identifier)
            usage = storage.published_usage(database, identifier)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
    return {
        **result,
        "shared_line_count": max(1, int(usage["line_count"])),
        "shared_repertoire_count": max(1, int(usage["repertoire_count"])),
    }


@router.post("/api/cards/{identifier}/prefix-split", response_model=PrefixSplitResponse | DeferredPrefixSplitResponse)
def prefix_split_accept(
    identifier: str,
    request: PrefixSplitRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    from .command_dispatch import dispatch_command

    return dispatch_command(
        "cards.prefix_split.accept",
        {"card_id": identifier, "request": request.model_dump(mode="json")},
        idempotency_key=idempotency_key,
    )


@router.post("/api/cards/{identifier}/prefix-split/reject")
def prefix_split_reject(
    identifier: str,
    request: PrefixSplitRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    from .command_dispatch import dispatch_command

    return dispatch_command(
        "cards.prefix_split.reject",
        {"card_id": identifier, "request": request.model_dump(mode="json")},
        idempotency_key=idempotency_key,
    )
