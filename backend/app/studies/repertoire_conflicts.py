"""Contradictory trained moves over supplied authored-line snapshots; no I/O."""
from __future__ import annotations
from collections import defaultdict
from functools import lru_cache
import json
import chess

def trained_move_index_from_snapshot(
    lines: tuple[tuple[str, ...], ...], *, conflict_fens: dict | None = None,
) -> dict:
    index: dict[tuple, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for line_id, repertoire_id, _name, trained_color, start_fen, moves_json in lines:
        try:
            board = chess.Board(start_fen)
            trained_turn = chess.WHITE if trained_color == "white" else chess.BLACK
            for move_uci in json.loads(moves_json):
                move = chess.Move.from_uci(move_uci)
                if move not in board.legal_moves:
                    break
                if board.turn == trained_turn:
                    # Conflict discovery only needs an EPD when two different
                    # moves reach the same position. The transposition key
                    # keeps the legal en-passant and castling distinctions.
                    position = (repertoire_id, board.epd() if conflict_fens is None
                                else board._transposition_key())
                    moves_at_position = index[position]
                    if (conflict_fens is not None and moves_at_position
                            and move_uci not in moves_at_position
                            and position not in conflict_fens):
                        conflict_fens[position] = board.epd()
                    moves_at_position[move_uci].add(line_id)
                board.push(move)
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    return index


@lru_cache(maxsize=4)
def conflicts_from_snapshot(lines: tuple[tuple[str, ...], ...]) -> tuple[dict, ...]:
    conflicts = []
    conflict_fens: dict = {}
    for position, moves in trained_move_index_from_snapshot(
        lines, conflict_fens=conflict_fens,
    ).items():
        if len(moves) < 2:
            continue
        current_repertoire_id = position[0]
        conflicts.append(
            {
                "repertoire_id": current_repertoire_id,
                "fen": conflict_fens[position],
                "moves": [
                    {"uci": move, "line_ids": sorted(line_ids)}
                    for move, line_ids in sorted(moves.items())
                ],
            }
        )
    return tuple(sorted(conflicts, key=lambda item: (item["repertoire_id"], item["fen"])))
