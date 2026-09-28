"""Restartable PostgreSQL game derivation, beginning with position indexing."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

import chess

from ..database import background_read_connection, connection
from .durable_tasks import (
    advance_task_slice_in_transaction, complete_task_slice_in_transaction,
    enqueue_compact_postgres_task_in_transaction, lock_current_slice,
)


@dataclass(frozen=True)
class PreparedGamePosition:
    game_id: str
    ply: int
    fen_key: str
    move_uci: str | None
    final_position: bool


def prepare_game_position(
    game_id: str, start_fen: str, moves_uci: tuple[str, ...], ply: int,
) -> PreparedGamePosition:
    """Replay outside the database and preserve SQLite's first-illegal-move boundary."""

    if ply < 0:
        raise ValueError("Game position cursor cannot be negative")
    board = chess.Board(start_fen)
    for move_uci in moves_uci[:ply]:
        move = chess.Move.from_uci(move_uci)
        if move not in board.legal_moves:
            raise ValueError("Game position cursor passed an illegal move")
        board.push(move)
    next_move = moves_uci[ply] if ply < len(moves_uci) else None
    if next_move is not None:
        try:
            legal_move = chess.Move.from_uci(next_move)
            if legal_move not in board.legal_moves:
                next_move = None
        except ValueError:
            next_move = None
    return PreparedGamePosition(
        game_id=game_id, ply=ply, fen_key=" ".join(board.fen().split()[:4]),
        move_uci=next_move, final_position=next_move is None,
    )


def execute_game_position_index_slice(task: dict[str, Any]) -> bool:
    """Publish one game occurrence, checking the derivation and task generations."""

    payload = task["payload"]
    game_id = str(payload["game_id"])
    derivation_version = int(payload["derivation_version"])
    ply = int(payload.get("cursor", 0))
    with background_read_connection() as database:
        game = database.execute(
            "SELECT start_fen,moves_json FROM imported_games WHERE id=?", (game_id,),
        ).fetchone()
    if game is None:
        with connection(background=True) as database:
            if lock_current_slice(database, task):
                complete_task_slice_in_transaction(database, task)
        return False
    prepared = prepare_game_position(
        game_id, game["start_fen"], tuple(json.loads(game["moves_json"])), ply,
    )
    with connection(background=True) as database:
        if not lock_current_slice(database, task):
            return False
        job = database.execute(
            "SELECT derivation_version,completed_phases,status FROM game_derivation_jobs "
            "WHERE game_id=? FOR UPDATE", (game_id,),
        ).fetchone()
        if (job is None or int(job["derivation_version"]) != derivation_version
                or int(job["completed_phases"]) > 0
                or job["status"] not in {"queued", "running"}):
            return complete_task_slice_in_transaction(database, task)
        database.execute(
            """INSERT INTO game_position_occurrences_staged(
                   game_id,derivation_version,ply,fen_key,move_uci)
               VALUES(?,?,?,?,?) ON CONFLICT(game_id,derivation_version,ply) DO UPDATE SET
               fen_key=excluded.fen_key,move_uci=excluded.move_uci""",
            (game_id, derivation_version, ply, prepared.fen_key, prepared.move_uci),
        )
        if prepared.final_position:
            staged_count = database.execute(
                "SELECT COUNT(*) FROM game_position_occurrences_staged "
                "WHERE game_id=? AND derivation_version=?",
                (game_id, derivation_version),
            ).fetchone()[0]
            if staged_count != ply + 1:
                raise RuntimeError("Game position index is incomplete; publication was withheld")
            database.execute(
                "UPDATE game_derivation_jobs SET completed_phases=1,"
                "phase='comparing_repertoire',published_position_version=? "
                "WHERE game_id=? AND derivation_version=?",
                (derivation_version, game_id, derivation_version),
            )
            enqueue_compact_postgres_task_in_transaction(
                database, "game_derivation_compare", game_id,
                {"game_id": game_id, "derivation_version": derivation_version,
                 "phase": "matches", "cursor": 0},
                priority=127,
            )
            return complete_task_slice_in_transaction(database, task)
        return advance_task_slice_in_transaction(
            database, task, next_phase="indexing_positions",
            next_payload={**payload, "cursor": ply + 1},
        )
