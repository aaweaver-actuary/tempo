"""Serialized foreground commands for durable guided game reviews."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import uuid
from typing import Any

import chess
from fastapi import HTTPException

from .command_gateway import register_command
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from tempo_legacy_database.adapters.games.guided_review import storage
from .services.guided_review import (
    _impact, _public_item, read_session_from_database,
    reconcile_guided_review_session, guided_review_error,
)


def start_review(database: PostgresConnection, payload: dict[str, Any]) -> dict:
    game_id = str(payload["game_id"])
    game = storage.locked_game(database, game_id)
    if game is None:
        raise HTTPException(404, "Game not found")
    existing = storage.locked_active_session(database, game_id, game["analysis_version"])
    if existing:
        return read_session_from_database(database, existing["id"])
    findings = storage.command_findings(database, game_id, game["analysis_version"])
    strongest_by_ply = {}
    for finding in findings:
        current = strongest_by_ply.get(finding["ply"])
        if current is None or _impact(finding) > _impact(current):
            strongest_by_ply[finding["ply"]] = finding
    ranked = sorted(strongest_by_ply.values(), key=_impact, reverse=True)
    finding_ids = [finding["id"] for finding in ranked[:5]]
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    storage.create_command_session_values(database, (session_id, game_id, game["analysis_version"], json.dumps(finding_ids),
         "active" if finding_ids else "complete", now, now))
    return read_session_from_database(database, session_id)


def submit_review_attempt(database: PostgresConnection, payload: dict[str, Any]) -> dict:
    session_id = str(payload["session_id"])
    move_uci = str(payload["move_uci"])
    session = storage.locked_session(database, session_id)
    if session is None:
        raise HTTPException(404, "Active guided review not found")
    session = reconcile_guided_review_session(database, session)
    if session["status"] != "active":
        return guided_review_error(404, "Guided review is complete")
    finding_ids = json.loads(session["finding_ids_json"])
    current_index = int(session["current_index"])
    if current_index >= len(finding_ids):
        raise HTTPException(404, "Guided review is complete")
    if payload.get("finding_id") != finding_ids[current_index]:
        return guided_review_error(409, "Guided review changed. Reload the session before trying again")
    finding = session["current_findings"][current_index]
    evidence = json.loads(finding["evidence_json"])
    try:
        board = chess.Board(evidence.get("fen"))
        move = chess.Move.from_uci(move_uci)
    except (ValueError, TypeError) as error:
        raise HTTPException(422, "Correction position or move is invalid") from error
    if move not in board.legal_moves:
        raise HTTPException(422, "Correction move is not legal from this position")
    expected_moves = set(evidence.get("expected", []))
    if evidence.get("best_move_uci"):
        expected_moves.add(evidence["best_move_uci"])
    correct = move_uci in expected_moves
    now = datetime.now(timezone.utc).isoformat()
    storage.insert_command_attempt_values(database, (session_id, finding["id"], move_uci, int(correct), now))
    next_index = current_index + 1
    storage.advance_session(database, next_index, "complete" if next_index >= len(finding_ids) else "active", now, session_id)
    return {"correct": correct, "revealed": _public_item(finding, reveal=True),
            "session": read_session_from_database(database, session_id)}


register_command("games.guided_review.start", start_review)
register_command("games.guided_review.attempt", submit_review_attempt)
