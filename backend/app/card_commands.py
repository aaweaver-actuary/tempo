"""Foreground PostgreSQL commands for user-edited cards."""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
from typing import Any

import chess
from fastapi import HTTPException

from .services.repertoire_game_refresh import refresh_game_publications_after_mutation
from .command_gateway import register_command
from app.repertoire.changes.application import validate_source_rows
from app.repertoire.changes.ports import ChangeError
from tempo_legacy_database.adapters.authored_repertoires import product_changes as change_storage
from tempo_legacy_database.adapters.authored_repertoires.card_edits import storage
from tempo_legacy_database.adapters.authored_repertoires.card_edits.storage import card_columns
from app.contracts.transport.content import CardRevisionRequest
from tempo_legacy_database.adapters.authored_repertoires.card_edits.storage import PostgresConnection
from app.chess.cards import card_id
from .services.postgres_integrity import (
    invalidate_integrity_in_transaction, request_integrity_scan_in_transaction,
)
from .services.postgres_opening_graph import request_graph_rebuild_in_transaction
from .services.repertoire_integrity import integrity_summary


def _validated_moves(starting_fen: str, supplied_moves: list[str]) -> list[str]:
    try:
        board = chess.Board(starting_fen)
    except ValueError as error:
        raise HTTPException(422, f"Invalid FEN: {error}") from error
    if not board.is_valid():
        raise HTTPException(422, "This position is not legal")
    moves: list[str] = []
    for supplied_move in supplied_moves:
        try:
            move = chess.Move.from_uci(supplied_move.lower())
        except ValueError as error:
            raise HTTPException(422, f"Invalid UCI move: {supplied_move}") from error
        if move not in board.legal_moves:
            raise HTTPException(422, f"Illegal move: {supplied_move}")
        moves.append(move.uci())
        board.push(move)
    if not moves:
        raise HTTPException(422, "A card needs at least one move")
    return moves


def _request_current_integrity_scan(
    database: PostgresConnection, repertoire_id: str, local_day: str,
) -> None:
    """Rescan the edited card when a current graph exists; a queued graph rescans itself."""

    invalidate_integrity_in_transaction(database, repertoire_id)
    current = storage.current_publication(database, repertoire_id)
    if current is not None and current[1] == "complete":
        request_integrity_scan_in_transaction(
            database, repertoire_id, int(current[0]), local_day,
        )
    else:
        graph_task = storage.graph_task(database, repertoire_id)
        if graph_task is None or graph_task[0] in {"complete", "failed", "superseded"}:
            request_graph_rebuild_in_transaction(database, repertoire_id, local_day)


@refresh_game_publications_after_mutation
def revise_card(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    identifier = str(payload["card_id"])
    request = CardRevisionRequest.model_validate(payload["request"])
    if request.expected_revision is None:
        raise HTTPException(422, "expected_revision is required for card edits")
    moves = _validated_moves(request.starting_fen, request.moves)
    from .services.canonical_prefix import ensure_line_in_scope, certify_admitted_route
    repertoire_ids = [row[0] for row in storage.card_scope(database, identifier)]
    validated_routes = {repertoire_id: ensure_line_in_scope(database, repertoire_id, request.starting_fen, moves, remember=False) for repertoire_id in repertoire_ids}
    for repertoire_id in sorted(repertoire_ids):
        sources = change_storage.lock_sources(database, repertoire_id)
        proposed = tuple(row for row in sources if row["source_id"] != f"card:{identifier}") + (
            dict(source_id=f"card:{identifier}", start_fen=request.starting_fen,
                 moves_json=json.dumps(moves), trained_color=next((row["trained_color"] for row in sources
                     if row["source_id"] == f"card:{identifier}"), None)),)
        try:
            validate_source_rows(repertoire_id, proposed)
        except ChangeError as error:
            raise HTTPException(409, str(error)) from error
    replacement_id = card_id(request.starting_fen, moves)
    storage.lock_prefix_transition(database)
    for locked_id in sorted({identifier, replacement_id}):
        storage.lock_card_edit(database, locked_id)
    from .card_deletion import require_card_not_deleted
    require_card_not_deleted(database, identifier)
    require_card_not_deleted(database, replacement_id)
    old = storage.locked_card(database, identifier)
    if old is None:
        raise HTTPException(404, "Card not found")
    if int(old["revision"] or 1) != request.expected_revision:
        raise HTTPException(409, "The card changed; refresh it and try again")
    if old["archived"] or old["superseded_by"] is not None:
        raise HTTPException(409, "The card was replaced; refresh it and try again")
    existing = storage.locked_replacement(database, replacement_id)
    revision = request.expected_revision + 1
    now = datetime.now(timezone.utc).isoformat()
    storage.record_revision(database, identifier, revision, old["start_fen"], old["moves_json"], request.history_mode, now)
    if replacement_id == identifier:
        storage.replace_content(database, request.starting_fen, moves, request.source_fen, revision, identifier)
    elif existing is not None:
        storage.mark_canonical(database, replacement_id)
        if request.history_mode == "preserve":
            storage.transfer_reviews(database, replacement_id, identifier)
        storage.complete_queued(database, identifier)
        storage.supersede(database, replacement_id, identifier)
    else:
        copied_fields = dict(old)
        copied_fields.update({
            "id": replacement_id, "start_fen": request.starting_fen,
            "moves_json": json.dumps(moves), "source_fen": request.source_fen,
            "revision": revision, "archived": 0, "superseded_by": None, "canonical_route_source": 1,
        })
        if request.history_mode == "reset":
            copied_fields.update({
                "due_date": date.today().isoformat(), "interval_days": 0,
                "repetitions": 0, "lapses": 0, "fsrs_card_json": None,
                "first_correct_at": None, "reinforcement_pending": 0,
                "stability": 0, "guided_review": 0, "state": "new",
                "scheduling_mode": "normal", "hard_correct_streak": 0,
                "recent_attempts_json": "[]",
            })
        columns = card_columns(database)
        storage.insert_replacement(database, columns, copied_fields)
        if request.history_mode == "preserve":
            storage.transfer_reviews(database, replacement_id, identifier)
        storage.rebind_queued(database, replacement_id, identifier)
        storage.supersede(database, replacement_id, identifier)
    if replacement_id != identifier:
        storage.copy_membership(database, replacement_id, identifier)
        storage.remove_membership(database, identifier)
    for repertoire_id, validated_route in validated_routes.items():
        storage.admit_membership(database, repertoire_id, replacement_id)
    for repertoire_id, validated_route in validated_routes.items():
        certify_admitted_route(database, repertoire_id, validated_route)
    repertoire_ids = [str(row[0]) for row in storage.card_repertoires(database, replacement_id)]
    if repertoire_ids:
        storage.mark_pending(database, replacement_id)
        for repertoire_id in repertoire_ids:
            _request_current_integrity_scan(database, repertoire_id, date.today().isoformat())
    return {
        "card_id": replacement_id,
        "replaced": replacement_id != identifier,
        "history_mode": request.history_mode,
        "revision": int(existing["revision"]) if replacement_id != identifier and existing else revision,
    }


@refresh_game_publications_after_mutation
def archive_card(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    identifier = str(payload["card_id"])
    storage.lock_prefix_transition(database)
    storage.lock_card_edit(database, identifier)
    card = storage.locked_archive_identity(database, identifier)
    if card is None:
        raise HTTPException(404, "Card not found")
    repertoire_ids = [str(row[0]) for row in storage.card_repertoires(database, identifier)]
    storage.archive(database, identifier)
    storage.complete_queued(database, identifier)
    for repertoire_id in repertoire_ids:
        _request_current_integrity_scan(database, repertoire_id, date.today().isoformat())
    integrity = {
        repertoire_id: integrity_summary(database, repertoire_id)
        for repertoire_id in repertoire_ids
    }
    return {"archived": True, "integrity": integrity}


register_command("cards.revise", revise_card)
register_command("cards.archive", archive_card)


@refresh_game_publications_after_mutation
def delete_card(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    from .card_deletion import permanent_delete_card
    return permanent_delete_card(database, str(payload["card_id"]), int(payload["expected_revision"]))


register_command("cards.delete", delete_card)
