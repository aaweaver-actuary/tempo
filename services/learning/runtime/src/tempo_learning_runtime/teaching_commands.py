"""Incremental teaching facts, borrowed from the gateway's transaction."""

from tempo_learning_database.adapters import teaching_states as storage
from datetime import datetime, timezone
import json
import chess
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from .card_deletion import require_card_not_deleted
from .review_conflicts import ReviewConflict


class TeachingStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=1)
    ply: int = Field(ge=0)


def fact(row):
    return {"cardId": row["card_id"], "revision": row["revision"],
            "ply": row["ply"], "taughtAt": row["taught_at"]}


def read_teaching(database, card_id):
    # Historical facts remain readable when their serving definition is retired.
    return {"states": [fact(row) for row in storage.teaching_rows(database, card_id)]}


def record_teaching(database, payload):
    card_id = payload["card_id"]
    request = TeachingStateRequest.model_validate(payload["teaching_state"])
    card = storage.lock_teaching_card(database, card_id)
    if card is None:
        raise HTTPException(404, "Card not found")
    require_card_not_deleted(database, card_id)
    if card["archived"] or not card["serving_active"] or card["superseded_by"]:
        raise ReviewConflict("card_retired", "Teaching definition is no longer serving")
    if card["revision"] != request.revision:
        raise ReviewConflict("card_revision_changed", "Teaching revision no longer matches")
    from .remediation_adapter import AcceptedDefinitions
    accepted = AcceptedDefinitions(database).for_card(card_id)
    if accepted is None or not accepted.active or accepted.card_revision != request.revision:
        raise ReviewConflict("card_revision_changed", "Teaching requires an active accepted executable revision")
    moves = json.loads(card["moves_json"])
    if card["content_type"] != "opening" or request.ply >= len(moves):
        raise HTTPException(422, "Teaching position is outside the accepted opening")
    board = chess.Board(card["start_fen"])
    for move in moves[:request.ply]:
        board.push_uci(move)
    if board.turn != (card["trained_color"] == "white"):
        raise HTTPException(422, "Teaching position is not a learner move")
    return fact(storage.record_teaching_state(
        database, card_id, request.revision, request.ply, datetime.now(timezone.utc).isoformat()))
