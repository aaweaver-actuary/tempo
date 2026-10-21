"""Foreground PostgreSQL commands for shortening an opening prefix card."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from tempo_legacy_database.adapters.foreground_controls import prefix_splits as storage
from .models import PrefixSplitRequest
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from tempo_legacy_database.adapters.queue_position_lock import lock_queue_date_for_position
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


def accept_prefix_split(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    card_id = str(payload["card_id"])
    request = PrefixSplitRequest.model_validate(payload["request"])
    _lock_prefix_card(database, card_id)
    lock_queue_date_for_position(database, date.today().isoformat())
    try:
        result = apply_prefix_split(database, card_id, request.expected_revision)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from error
    if not result["idempotent"]:
        linked_repertoire_ids = {
            str(row[0]) for row in storage.linked_repertoire_rows(
                database, result["parent"]["card_id"], result["continuation"]["card_id"],
            )
        }
        storage.mark_split_pending_validation(
            database, result["parent"]["card_id"], result["continuation"]["card_id"],
        )
        for repertoire_id in sorted(linked_repertoire_ids):
            invalidate_integrity_in_transaction(database, repertoire_id)
            request_graph_rebuild_in_transaction(
                database, repertoire_id, date.today().isoformat(),
            )
    return {**result, **_shared_usage(database, card_id)}


def reject_prefix_split(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, int]:
    card_id = str(payload["card_id"])
    request = PrefixSplitRequest.model_validate(payload["request"])
    _lock_prefix_card(database, card_id)
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
