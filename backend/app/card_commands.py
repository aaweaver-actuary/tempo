"""Foreground PostgreSQL commands for user-edited cards."""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
from typing import Any

import chess
from fastapi import HTTPException

from .services.repertoire_game_refresh import refresh_game_publications_after_mutation
from .command_gateway import register_command
from .database import card_columns
from .models import CardRevisionRequest
from .postgres_store import PostgresConnection
from .services.cards import card_id
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
    current = database.execute_native(
        "SELECT publication.generation,task.state FROM opening_graph_publications publication "
        "JOIN background_tasks task ON task.kind='opening_graph_rebuild' "
        "AND task.deduplication_key=publication.repertoire_id "
        "AND task.generation=publication.generation "
        "WHERE publication.repertoire_id=%s", (repertoire_id,),
    ).fetchone()
    if current is not None and current[1] == "complete":
        request_integrity_scan_in_transaction(
            database, repertoire_id, int(current[0]), local_day,
        )
    else:
        graph_task = database.execute_native(
            "SELECT state FROM background_tasks WHERE kind='opening_graph_rebuild' "
            "AND deduplication_key=%s", (repertoire_id,),
        ).fetchone()
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
    repertoire_ids = [row[0] for row in database.execute(
        "SELECT repertoire_id FROM repertoire_cards WHERE card_id=? UNION SELECT repertoire_id FROM cards WHERE id=? ORDER BY repertoire_id", (identifier, identifier))]
    validated_routes = {repertoire_id: ensure_line_in_scope(database, repertoire_id, request.starting_fen, moves, remember=False) for repertoire_id in repertoire_ids}
    replacement_id = card_id(request.starting_fen, moves)
    for locked_id in sorted({identifier, replacement_id}):
        database.execute_native(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (f"tempo:card-edit:{locked_id}",),
        )
    from .card_deletion import require_card_not_deleted
    require_card_not_deleted(database, identifier)
    require_card_not_deleted(database, replacement_id)
    old = database.execute_native(
        "SELECT * FROM cards WHERE id=%s FOR UPDATE", (identifier,),
    ).fetchone()
    if old is None:
        raise HTTPException(404, "Card not found")
    if int(old["revision"] or 1) != request.expected_revision:
        raise HTTPException(409, "The card changed; refresh it and try again")
    if old["archived"] or old["superseded_by"] is not None:
        raise HTTPException(409, "The card was replaced; refresh it and try again")
    existing = database.execute_native(
        "SELECT id,revision FROM cards WHERE id=%s FOR UPDATE", (replacement_id,),
    ).fetchone()
    revision = request.expected_revision + 1
    now = datetime.now(timezone.utc).isoformat()
    database.execute_native(
        "INSERT INTO card_revisions(card_id,revision,start_fen,moves_json,history_mode,created_at) "
        "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(card_id,revision) DO NOTHING",
        (identifier, revision, old["start_fen"], old["moves_json"], request.history_mode, now),
    )
    if replacement_id == identifier:
        database.execute_native(
            "UPDATE cards SET start_fen=%s,moves_json=%s,source_fen=%s,revision=%s,canonical_route_source=1 WHERE id=%s",
            (request.starting_fen, json.dumps(moves), request.source_fen, revision, identifier),
        )
    elif existing is not None:
        database.execute_native("UPDATE cards SET canonical_route_source=1 WHERE id=%s", (replacement_id,))
        if request.history_mode == "preserve":
            database.execute_native(
                "UPDATE reviews SET card_id=%s WHERE card_id=%s", (replacement_id, identifier),
            )
        database.execute_native(
            "UPDATE daily_queue SET status='complete' WHERE card_id=%s AND status='queued'",
            (identifier,),
        )
        database.execute_native(
            "UPDATE cards SET archived=1,superseded_by=%s WHERE id=%s",
            (replacement_id, identifier),
        )
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
        database.execute_native(
            f"INSERT INTO cards({','.join(columns)}) VALUES({','.join('%s' for _ in columns)})",
            tuple(copied_fields.get(column) for column in columns),
        )
        if request.history_mode == "preserve":
            database.execute_native(
                "UPDATE reviews SET card_id=%s WHERE card_id=%s", (replacement_id, identifier),
            )
        database.execute_native(
            "UPDATE daily_queue SET card_id=%s WHERE card_id=%s AND status='queued'",
            (replacement_id, identifier),
        )
        database.execute_native(
            "UPDATE cards SET archived=1,superseded_by=%s WHERE id=%s",
            (replacement_id, identifier),
        )
    if replacement_id != identifier:
        database.execute_native(
            "INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) "
            "SELECT repertoire_id,%s,1 FROM repertoire_cards WHERE card_id=%s "
            "ON CONFLICT(repertoire_id,card_id) DO UPDATE SET canonical_route_source=1",
            (replacement_id, identifier),
        )
        database.execute_native(
            "DELETE FROM repertoire_cards WHERE card_id=%s", (identifier,),
        )
    for repertoire_id, validated_route in validated_routes.items():
        database.execute("INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(?,?,1) ON CONFLICT(repertoire_id,card_id) DO UPDATE SET canonical_route_source=1", (repertoire_id, replacement_id))
    for repertoire_id, validated_route in validated_routes.items():
        certify_admitted_route(database, repertoire_id, validated_route)
    repertoire_ids = [str(row[0]) for row in database.execute_native(
        "SELECT repertoire_id FROM repertoire_cards WHERE card_id=%s ORDER BY repertoire_id",
        (replacement_id,),
    )]
    if repertoire_ids:
        database.execute_native(
            "UPDATE cards SET pending_validation=1 WHERE id=%s", (replacement_id,),
        )
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
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (f"tempo:card-edit:{identifier}",),
    )
    card = database.execute_native(
        "SELECT id FROM cards WHERE id=%s FOR UPDATE", (identifier,),
    ).fetchone()
    if card is None:
        raise HTTPException(404, "Card not found")
    repertoire_ids = [str(row[0]) for row in database.execute_native(
        "SELECT repertoire_id FROM repertoire_cards WHERE card_id=%s ORDER BY repertoire_id",
        (identifier,),
    )]
    database.execute_native(
        "UPDATE cards SET archived=1 WHERE id=%s", (identifier,),
    )
    database.execute_native(
        "UPDATE daily_queue SET status='complete' WHERE card_id=%s AND status='queued'",
        (identifier,),
    )
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
