"""Foreground PostgreSQL command for a user-authored repertoire branch."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from typing import Any

import chess
from fastapi import HTTPException

from .command_gateway import register_command
from .models import BranchRequest, RemoveBranchRequest
from .postgres_store import PostgresConnection
from .services.cards import card_id
from .services.postgres_opening_graph import request_graph_rebuild_in_transaction
from .services.postgres_integrity import invalidate_integrity_in_transaction
from .services.postgres_coverage_seed import request_coverage_seed_in_transaction
from .services.repertoire_integrity import integrity_summary


def add_repertoire_branch(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    request = BranchRequest.model_validate(payload)
    try:
        board = chess.Board(request.starting_fen)
        moves = []
        for value in request.moves:
            move = chess.Move.from_uci(value.lower())
            if move not in board.legal_moves:
                raise ValueError("Branch contains an illegal move")
            moves.append(move.uci())
            board.push(move)
    except (ValueError, chess.InvalidMoveError) as error:
        raise HTTPException(422, "Branch contains an illegal move") from error
    repertoire_id = request.repertoire_id
    line_id = hashlib.sha256(
        f"{repertoire_id}\0{card_id(request.starting_fen, moves)}".encode(),
    ).hexdigest()
    if database.execute_native(
        "SELECT 1 FROM repertoires WHERE id=%s FOR UPDATE", (repertoire_id,),
    ).fetchone() is None:
        raise HTTPException(404, "Repertoire not found")
    inserted = database.execute_native(
        "INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING RETURNING id",
        (line_id, repertoire_id, request.name, request.trained_color,
         request.starting_fen, json.dumps(moves), datetime.now(timezone.utc).isoformat()),
    ).fetchone()
    depth = database.execute_native(
        "SELECT initial_depth FROM settings WHERE id=1"
    ).fetchone()[0]
    database.execute_native(
        "INSERT INTO repertoire_line_training_depths(line_id,learner_decision_count) "
        "VALUES(%s,%s) ON CONFLICT(line_id) DO UPDATE SET "
        "learner_decision_count=excluded.learner_decision_count",
        (line_id, depth),
    )
    if request.source_gap_id:
        try:
            gap_node_id, gap_move_uci = request.source_gap_id.split(":", 1)
        except ValueError as error:
            raise HTTPException(422, "Invalid repertoire coverage gap") from error
        gap = database.execute_native(
            "SELECT candidate.move_uci,node.repertoire_id FROM repertoire_coverage_candidates candidate "
            "JOIN repertoire_coverage_nodes node ON node.id=candidate.node_id "
            "WHERE candidate.node_id=%s AND candidate.move_uci=%s FOR UPDATE OF candidate",
            (gap_node_id, gap_move_uci),
        ).fetchone()
        if gap is None or gap["repertoire_id"] != repertoire_id:
            raise HTTPException(422, "Coverage gap does not belong to this repertoire")
        if not moves or moves[0] != gap_move_uci or len(moves) < 2:
            raise HTTPException(
                422, "A resolved gap must include the missing opponent move and your response",
            )
        database.execute_native(
            "UPDATE repertoire_coverage_candidates SET covered=1 "
            "WHERE node_id=%s AND move_uci=%s",
            (gap_node_id, gap_move_uci),
        )
    invalidate_integrity_in_transaction(database, repertoire_id)
    request_graph_rebuild_in_transaction(database, repertoire_id, date.today().isoformat())
    if inserted is not None:
        request_coverage_seed_in_transaction(
            database, repertoire_id, automatic=True, supersede_active=True,
        )
    return {"id": line_id, "duplicate": inserted is None, "moves": moves,
            "integrity": integrity_summary(database, repertoire_id)}


register_command("repertoire.branch.add", add_repertoire_branch)


def remove_repertoire_branch(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    request = RemoveBranchRequest.model_validate(payload)
    if not request.moves:
        raise HTTPException(422, "Choose a nonempty branch to remove")
    try:
        board = chess.Board(request.starting_fen)
        moves = [move.lower() for move in request.moves]
        for move in moves:
            board.push_uci(move)
    except ValueError as error:
        raise HTTPException(422, "Branch contains an illegal move") from error
    repertoire_id = request.repertoire_id
    if database.execute_native(
        "SELECT id FROM repertoires WHERE id=%s FOR UPDATE", (repertoire_id,),
    ).fetchone() is None:
        raise HTTPException(404, "Repertoire not found")
    position_key = " ".join(request.starting_fen.split()[:4])
    stored_lines = database.execute_native(
        "SELECT id,start_fen,moves_json FROM repertoire_lines "
        "WHERE repertoire_id=%s ORDER BY id", (repertoire_id,),
    ).fetchall()
    matching_line_ids = []
    for stored_line in stored_lines:
        if " ".join(str(stored_line["start_fen"]).split()[:4]) != position_key:
            continue
        try:
            if json.loads(stored_line["moves_json"])[:len(moves)] == moves:
                matching_line_ids.append(str(stored_line["id"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    if matching_line_ids:
        database.execute_native(
            "DELETE FROM repertoire_lines WHERE id=ANY(%s::text[])",
            (matching_line_ids,),
        )
        invalidate_integrity_in_transaction(database, repertoire_id)
        request_graph_rebuild_in_transaction(database, repertoire_id, date.today().isoformat())
        request_coverage_seed_in_transaction(
            database, repertoire_id, automatic=True, supersede_active=True,
        )
    return {
        "deleted_line_count": len(matching_line_ids),
        "deleted_card_count": 0,
        "retained_line_count": len(stored_lines) - len(matching_line_ids),
        "integrity": integrity_summary(database, repertoire_id),
    }


register_command("repertoire.branch.remove", remove_repertoire_branch)
