"""Foreground PostgreSQL commands for shortening an opening prefix card."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .models import PrefixSplitRequest
from .postgres_store import PostgresConnection
from .queue_position_lock import lock_queue_date_for_position
from .services.postgres_integrity import invalidate_integrity_in_transaction
from .services.postgres_opening_graph import request_graph_rebuild_in_transaction
from .services.prefix_split import apply_prefix_split, preview_prefix_split


def _lock_prefix_card(database: PostgresConnection, card_id: str) -> None:
    if database.execute_native(
        "SELECT id FROM cards WHERE id=%s FOR UPDATE", (card_id,),
    ).fetchone() is None:
        raise HTTPException(404, "Card not found")


def _shared_usage(database: PostgresConnection, card_id: str) -> dict[str, int]:
    usage = database.execute(
        "SELECT COUNT(DISTINCT step.line_id) line_count,"
        "COUNT(DISTINCT step.repertoire_id) repertoire_count "
        "FROM opening_graph_steps step "
        "JOIN opening_graph_publications publication "
        "ON publication.repertoire_id=step.repertoire_id "
        "AND publication.generation=step.generation "
        "WHERE step.card_id=?", (card_id,),
    ).fetchone()
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
            str(row[0]) for row in database.execute(
                "SELECT repertoire_id FROM repertoire_cards WHERE card_id IN (?,?)",
                (result["parent"]["card_id"], result["continuation"]["card_id"]),
            )
        }
        database.execute(
            "UPDATE cards SET pending_validation=1 WHERE id IN (?,?)",
            (result["parent"]["card_id"], result["continuation"]["card_id"]),
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
    latest_failed_review_id = database.execute(
        "SELECT COALESCE(MAX(id),0) FROM reviews "
        "WHERE card_id=? AND rating='again' AND source_kind='study' "
        "AND invalidated_at IS NULL", (card_id,),
    ).fetchone()[0]
    database.execute(
        "UPDATE cards SET prefix_split_rejected_after_review_id=? WHERE id=?",
        (latest_failed_review_id, card_id),
    )
    return {"rejected_after_review_id": int(latest_failed_review_id)}


register_command("cards.prefix_split.accept", accept_prefix_split)
register_command("cards.prefix_split.reject", reject_prefix_split)
