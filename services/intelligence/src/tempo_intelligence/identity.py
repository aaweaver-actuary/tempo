"""Canonical source inputs, independent of caller policy or repertoire identity."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import chess
from pydantic import BaseModel
from tempo_chess_core import canonical_fen_key

if TYPE_CHECKING:
    from .models import Snapshot, Source


def canonical_json(value: object) -> str:
    """Serialize validated inputs without guessing how unknown objects serialize."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid_canonical_payload") from None


def digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def canonical_position(fen: str) -> tuple[str, str]:
    """Validate standard chess and normalize irrelevant clocks and legal EP state."""
    try:
        board = chess.Board(fen)
        if not board.is_valid():
            raise ValueError("invalid_position")
        position_key = canonical_fen_key(board.fen(en_passant="legal"))
    except (TypeError, ValueError, IndexError):
        raise ValueError("invalid_position") from None
    return position_key, position_key + " 0 1"


def legal_universe_digest(position_key: str, legal_moves: list[str], version: str) -> str:
    return digest({"legal_universe_version": version, "position_key": position_key, "legal_moves": sorted(legal_moves)})


def source_descriptor(snapshot: Snapshot, source: Source) -> dict:
    position_key, canonical_fen = canonical_position(snapshot.fen)
    legal_moves = sorted(snapshot.legal_moves)
    descriptor = {
        "schema_version": 1,
        "kind": source.kind,
        "canonical_fen": canonical_fen,
        "position_key": position_key,
        "legal_moves": legal_moves,
        "legal_universe_digest": snapshot.legal_universe_digest,
    }
    if source.kind == "maia":
        descriptor.update({"rating": source.rating, "model_id": source.model_id, "model_version": source.model_version, "artifact_sha256": source.artifact_sha256})
        return descriptor
    parameters = {"fen": canonical_fen, "moves": len(legal_moves), "topGames": 0, "since": source.since, "until": source.until}
    if source.kind == "lichess":
        parameters.update({"variant": "standard", "speeds": ",".join(source.speeds), "ratings": ",".join(str(rating) for rating in source.ratings), "recentGames": 0, "history": False})
    descriptor["parameters"] = parameters
    return descriptor
