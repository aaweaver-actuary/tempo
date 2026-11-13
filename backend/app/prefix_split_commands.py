"""Foreground PostgreSQL commands for shortening an opening prefix card."""

from __future__ import annotations

from typing import Any
import uuid
import json

from fastapi import HTTPException

from .services.study_calendar import capture_study_calendar

from .command_gateway import CommandPrerequisitePending, register_command
from tempo_storage.foreground_controls import prefix_splits as storage
from tempo_storage.authored_repertoires.opening_mutations import prefix_splits as split_storage
from tempo_storage.study import deferred_prefix_splits as deferred_storage
from app.contracts.transport.content import PrefixSplitRequest
from tempo_storage.postgres import PostgresConnection
from tempo_storage.queue_position_lock import (
    lock_queue_date_for_position,
)
from .services.postgres_integrity import invalidate_integrity_in_transaction
from .services.postgres_opening_graph import request_graph_rebuild_in_transaction
from .services.prefix_split import apply_prefix_split, preview_prefix_split


def _lock_prefix_card(database: PostgresConnection, card_id: str) -> None:
    if storage.locked_prefix_card(database, card_id) is None:
        raise HTTPException(404, "Card not found")


def _shared_usage(database: PostgresConnection, card_id: str) -> dict[str, int]:
    usage = storage.published_usage(database, card_id)
    return {
        "shared_line_count": max(1, int(usage["line_count"])),
        "shared_repertoire_count": max(1, int(usage["repertoire_count"])),
    }


def accept_prefix_split(
    database: PostgresConnection, payload: dict[str, Any]
) -> dict[str, Any]:
    card_id = str(payload["card_id"])
    request = PrefixSplitRequest.model_validate(payload["request"])
    _lock_prefix_card(database, card_id)
    if request.queue_entry_id is not None and not split_storage.matches_queue_entry(database, card_id, request.queue_entry_id):
        raise HTTPException(409, "The queue attempt no longer matches this card")
    if request.queue_entry_id is not None and split_storage.prior_split(database, card_id) is None:
        try:
            preview = preview_prefix_split(database, card_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if preview["source_revision"] != request.expected_revision:
            raise HTTPException(409, "The card changed. Refresh the queue and try again")
        if not deferred_storage.issued_origin(database, card_id, request.expected_revision, request.queue_entry_id):
            raise HTTPException(409, "The original queue attempt was not issued")
        intent = deferred_storage.create_intent(database, str(uuid.uuid4()), card_id, request.expected_revision, request.queue_entry_id)
        if intent["state"] == "applied":
            return dict(intent["result_json"])
        return {"status": intent["state"], "intent_id": intent["intent_id"], "card_id": card_id,
                "source_revision": request.expected_revision, "queue_entry_id": request.queue_entry_id,
                "reason": intent["reason"]}
    return _apply_split(database, card_id, request.expected_revision)


def _apply_split(database: PostgresConnection, card_id: str, expected_revision: int) -> dict[str, Any]:
    calendar = capture_study_calendar(database)
    for day in sorted(set(split_storage.source_queue_days(database, card_id)) | {calendar.day.isoformat()}):
        lock_queue_date_for_position(database, day)
    try:
        result = apply_prefix_split(database, card_id, expected_revision, calendar=calendar)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error
    if not result["idempotent"]:
        linked_repertoire_ids = {
            str(row[0])
            for row in storage.linked_repertoire_rows(
                database,
                result["parent"]["card_id"],
                result["continuation"]["card_id"],
            )
        }
        storage.mark_split_pending_validation(
            database,
            result["parent"]["card_id"],
            result["continuation"]["card_id"],
        )
        for repertoire_id in sorted(linked_repertoire_ids):
            invalidate_integrity_in_transaction(database, repertoire_id)
            request_graph_rebuild_in_transaction(
                database,
                repertoire_id,
                calendar.day.isoformat(),
            )
    return {**result, **_shared_usage(database, card_id)}


def apply_deferred_prefix_split(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    intent_id = str(payload["intent_id"])
    snapshot = deferred_storage.intent(database, intent_id)
    if snapshot is None:
        raise HTTPException(404, "Split intent not found")
    _lock_prefix_card(database, snapshot["card_id"])
    intent = deferred_storage.intent(database, intent_id, lock=True)
    if intent is None:
        raise HTTPException(404, "Split intent not found")
    if intent["state"] == "applied":
        return {"status": "applied", "intent_id": intent_id, "result": intent["result_json"]}
    if intent["state"] != "ready":
        return {"status": intent["state"], "intent_id": intent_id}
    matches = deferred_storage.matching_reviews(database, intent["card_id"], intent["source_revision"], intent["queue_entry_id"])
    from .services.deferred_prefix_split_recovery import valid_original_review_receipt
    if (len(matches) != 1 or matches[0]["attempt_id"] != intent["review_attempt_id"]
            or matches[0]["review_id"] != intent["review_id"] or not valid_original_review_receipt(matches[0])):
        deferred_storage.set_held(database, intent_id, "original_review_unavailable")
        return {"status": "held", "intent_id": intent_id, "reason": "original_review_unavailable"}
    if split_storage.has_active_issued_attempt(database, intent["card_id"]):
        raise CommandPrerequisitePending("Another issued attempt must be saved before this split can apply.")
    try:
        preview = preview_prefix_split(database, intent["card_id"])
        if preview["source_revision"] != intent["source_revision"]:
            raise ValueError("revision_changed")
    except (KeyError, ValueError):
        deferred_storage.set_held(database, intent_id, "source_changed")
        return {"status": "held", "intent_id": intent_id, "reason": "source_changed"}
    result = _apply_split(database, intent["card_id"], intent["source_revision"])
    if not deferred_storage.set_applied(database, intent_id, json.dumps(result, separators=(",", ":"))):
        raise RuntimeError("Split intent changed before publication")
    return {"status": "applied", "intent_id": intent_id, "result": result}


def reject_prefix_split(
    database: PostgresConnection, payload: dict[str, Any]
) -> dict[str, int]:
    card_id = str(payload["card_id"])
    request = PrefixSplitRequest.model_validate(payload["request"])
    _lock_prefix_card(database, card_id)
    if request.queue_entry_id is not None and not split_storage.matches_queue_entry(database, card_id, request.queue_entry_id):
        raise HTTPException(409, "The queue attempt no longer matches this card")
    try:
        preview = preview_prefix_split(database, card_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if preview["source_revision"] != request.expected_revision:
        raise HTTPException(409, "The card changed; refresh it and try again")
    latest_failed_review_id = storage.latest_failed_study_review_id(database, card_id)
    storage.store_split_rejection(database, latest_failed_review_id, card_id)
    return {"rejected_after_review_id": int(latest_failed_review_id)}


register_command("cards.prefix_split.accept", accept_prefix_split)
register_command("cards.prefix_split.reject", reject_prefix_split)
register_command("cards.prefix_split.apply_deferred", apply_deferred_prefix_split)
