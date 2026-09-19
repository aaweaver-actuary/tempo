"""Detect contradictory trained-player responses inside one repertoire."""

from __future__ import annotations

from collections import defaultdict
import json

import chess

from .repertoire_comparison import canonical_fen


def trained_move_index(database, repertoire_id: str | None = None) -> dict:
    parameters: tuple[str, ...] = ()
    where = ""
    if repertoire_id:
        where = "WHERE repertoire_id=?"
        parameters = (repertoire_id,)
    rows = database.execute(
        f"SELECT id,repertoire_id,name,trained_color,start_fen,moves_json FROM repertoire_lines {where}",
        parameters,
    ).fetchall()
    index: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for row in rows:
        try:
            board = chess.Board(row["start_fen"])
            trained_turn = chess.WHITE if row["trained_color"] == "white" else chess.BLACK
            for move_uci in json.loads(row["moves_json"]):
                move = chess.Move.from_uci(move_uci)
                if move not in board.legal_moves:
                    break
                if board.turn == trained_turn:
                    index[(row["repertoire_id"], canonical_fen(board.fen()))][
                        move_uci
                    ].add(row["id"])
                board.push(move)
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    return index


def find_repertoire_conflicts(database, repertoire_id: str | None = None) -> list[dict]:
    conflicts = []
    for (current_repertoire_id, fen), moves in trained_move_index(
        database, repertoire_id
    ).items():
        if len(moves) < 2:
            continue
        conflicts.append(
            {
                "repertoire_id": current_repertoire_id,
                "fen": fen,
                "moves": [
                    {"uci": move, "line_ids": sorted(line_ids)}
                    for move, line_ids in sorted(moves.items())
                ],
            }
        )
    return sorted(conflicts, key=lambda item: (item["repertoire_id"], item["fen"]))
