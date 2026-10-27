"""Foreground PostgreSQL command for a user-authored repertoire branch."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from typing import Any

import chess
from fastapi import HTTPException

from .services.repertoire_game_refresh import refresh_game_publications_after_mutation
from .command_gateway import register_command
from app.repertoire.changes.application import validate_source_rows
from app.repertoire.changes.ports import ChangeError
from tempo_legacy_database.adapters.authored_repertoires import product_changes as change_storage
from app.contracts.transport.authored_lines import BranchRequest, RemoveBranchRequest
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from tempo_legacy_database.adapters.authored_repertoires import branches as storage
from app.chess.cards import card_id
from .services.canonical_prefix import ensure_line_in_scope, certify_admitted_route
from .services.postgres_opening_graph import request_graph_rebuild_in_transaction
from .services.postgres_integrity import invalidate_integrity_in_transaction
from .services.postgres_coverage_seed import request_coverage_seed_in_transaction
from .services.repertoire_integrity import integrity_summary


@refresh_game_publications_after_mutation
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
    if storage.lock_repertoire_for_add(database, repertoire_id) is None:
        raise HTTPException(404, "Repertoire not found")
    try:
        validate_source_rows(repertoire_id, change_storage.lock_sources(database, repertoire_id) + (
            dict(source_id=f"branch:{line_id}", start_fen=request.starting_fen,
                 moves_json=json.dumps(moves), trained_color=request.trained_color),))
    except ChangeError as error:
        raise HTTPException(409, str(error)) from error
    validated_route = ensure_line_in_scope(database, repertoire_id, request.starting_fen, moves, remember=False)
    inserted = storage.insert_line_values(
        database,
        (line_id, repertoire_id, request.name, request.trained_color,
         request.starting_fen, json.dumps(moves), datetime.now(timezone.utc).isoformat()),
    )
    depth = storage.initial_training_depth(database)
    storage.set_line_training_depth(database, line_id, depth)
    if request.source_gap_id:
        try:
            gap_node_id, gap_move_uci = request.source_gap_id.split(":", 1)
        except ValueError as error:
            raise HTTPException(422, "Invalid repertoire coverage gap") from error
        gap = storage.lock_source_gap(database, gap_node_id, gap_move_uci)
        if gap is None or gap["repertoire_id"] != repertoire_id:
            raise HTTPException(422, "Coverage gap does not belong to this repertoire")
        if not moves or moves[0] != gap_move_uci or len(moves) < 2:
            raise HTTPException(
                422, "A resolved gap must include the missing opponent move and your response",
            )
        storage.mark_gap_covered(database, gap_node_id, gap_move_uci)
    if inserted is not None:
        certify_admitted_route(database, repertoire_id, validated_route)
    invalidate_integrity_in_transaction(database, repertoire_id)
    request_graph_rebuild_in_transaction(database, repertoire_id, date.today().isoformat())
    if inserted is not None:
        request_coverage_seed_in_transaction(
            database, repertoire_id, automatic=True, supersede_active=True,
        )
    return {"id": line_id, "duplicate": inserted is None, "moves": moves,
            "integrity": integrity_summary(database, repertoire_id)}


register_command("repertoire.branch.add", add_repertoire_branch)


@refresh_game_publications_after_mutation
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
    if storage.lock_repertoire_for_remove(database, repertoire_id) is None:
        raise HTTPException(404, "Repertoire not found")
    position_key = " ".join(request.starting_fen.split()[:4])
    stored_lines = storage.stored_repertoire_lines(database, repertoire_id)
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
        storage.delete_matching_lines(database, matching_line_ids)
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
