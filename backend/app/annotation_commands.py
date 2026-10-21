"""Foreground PostgreSQL position-note writes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import chess
from fastapi import HTTPException

from .command_gateway import register_command
from .models import PositionAnnotationRequest
from tempo_legacy_database.adapters.commands import annotations as storage
from tempo_legacy_database.adapters.postgres_store import PostgresConnection


def save_position_annotation(
    database: PostgresConnection, payload: dict[str, Any]
) -> dict[str, Any]:
    repertoire_id = str(payload["repertoire_id"])
    request = PositionAnnotationRequest.model_validate(payload["annotation"])
    try:
        fen_key = " ".join(chess.Board(request.fen).fen().split()[:4])
    except ValueError as error:
        raise HTTPException(422, f"Invalid FEN: {error}") from error
    if storage.repertoire_for_update(database, repertoire_id) is None:
        raise HTTPException(404, "Repertoire not found")
    comment = request.comment.strip()
    arrows = [arrow.model_dump(by_alias=True) for arrow in request.arrows]
    squares = [square.model_dump() for square in request.squares]
    updated_at = datetime.now(timezone.utc).isoformat()
    if not comment and not arrows and not squares:
        storage.delete_position_annotation(database, repertoire_id, fen_key)
    else:
        storage.save_position_annotation(
            database, repertoire_id, fen_key, comment, arrows, squares, updated_at,
        )
    return {
        "repertoireId": repertoire_id,
        "fenKey": fen_key,
        "comment": comment,
        "arrows": arrows,
        "squares": squares,
        "updatedAt": updated_at,
    }


register_command("repertoires.annotation.save", save_position_annotation)
