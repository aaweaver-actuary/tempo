"""Explicit opening scope, independent of scheduled opening prefix cards."""

from __future__ import annotations

import json
import re

import chess


def position_key(fen: str) -> str:
    return " ".join(chess.Board(fen).fen().split()[:4])


def parse_prefix(movetext: str) -> list[str]:
    """Parse one SAN sequence; variations, null moves, and headers are not prefixes."""
    if any(character in movetext for character in "(){}[];"):
        raise ValueError("Enter one sequence of SAN moves without variations or PGN headers")
    tokens = re.sub(r"\d+\.(?:\.\.)?", " ", movetext).split()
    if len(tokens) > 160:
        raise ValueError("A canonical prefix can contain at most 160 moves")
    board = chess.Board()
    moves: list[str] = []
    for token in tokens:
        try:
            move = board.parse_san(token)
        except ValueError as error:
            raise ValueError(f"Invalid SAN move {token!r} after {prefix_projection(moves)['san'] or 'the starting position'}") from error
        if not move:
            raise ValueError("A canonical prefix cannot contain a null move")
        moves.append(move.uci())
        board.push(move)
    return moves


def prefix_projection(moves: list[str], revision: int = 0) -> dict:
    board = chess.Board()
    notation: list[str] = []
    for move_uci in moves:
        move = chess.Move.from_uci(move_uci)
        if move not in board.legal_moves:
            raise ValueError("Canonical prefix contains an illegal move")
        if board.turn == chess.WHITE:
            notation.append(f"{board.fullmove_number}.")
        notation.append(board.san(move))
        board.push(move)
    return {"moves_uci": moves, "san": " ".join(notation),
            "ending_fen": board.fen(), "revision": revision}


def read_prefix(database, repertoire_id: str, *, lock: bool = False) -> dict:
    suffix = " FOR UPDATE" if lock and hasattr(database, "execute_native") else ""
    row = database.execute(
        "SELECT canonical_prefix_moves_json,canonical_prefix_revision,"
        "canonical_prefix_preview_id,scope_source_revision FROM repertoires WHERE id=?" + suffix,
        (repertoire_id,),
    ).fetchone()
    if row is None:
        return {"moves": [], "revision": 0, "preview_id": None, "source_revision": 0}
    return {"moves": json.loads(row["canonical_prefix_moves_json"]),
            "revision": int(row["canonical_prefix_revision"]),
            "preview_id": row["canonical_prefix_preview_id"],
            "source_revision": int(row["scope_source_revision"])}


def game_in_scope(starting_fen: str, moves: list[str], prefix_moves: list[str]) -> bool:
    if not prefix_moves:
        return True
    return (position_key(starting_fen) == position_key(chess.STARTING_FEN)
            and len(moves) >= len(prefix_moves)
            and moves[:len(prefix_moves)] == prefix_moves)


def validate_scoped_line(starting_fen: str, moves: list[str], prefix_moves: list[str],
                         origin: list[str] | None) -> dict:
    """Validate one line from a verified route, keeping its original training start."""
    if position_key(starting_fen) == position_key(chess.STARTING_FEN):
        origin = []
    if origin is None and prefix_moves:
        return {"status": "pending", "reason": "No verified route from the canonical prefix", "disagreement_ply": None}
    route = [*(origin or []), *moves]
    if prefix_moves:
        for ply, (actual, expected) in enumerate(zip(route, prefix_moves)):
            if actual != expected:
                board = chess.Board()
                for earlier in route[:ply]:
                    board.push_uci(earlier)
                return {"status": "conflict", "reason":
                        f"Move {ply + 1}: expected {board.san(chess.Move.from_uci(expected))}, "
                        f"found {board.san(chess.Move.from_uci(actual))}", "disagreement_ply": ply}
    board = chess.Board(starting_fen)
    if origin is not None:
        origin_board = chess.Board()
        for move_uci in origin:
            origin_board.push_uci(move_uci)
        if position_key(origin_board.fen()) != position_key(starting_fen):
            return {"status": "conflict", "reason": "Saved route does not reach this line's starting position", "disagreement_ply": None}
    positions = []
    for ply in range(len(moves) + 1):
        if origin is not None:
            absolute_ply = len(origin) + ply
            positions.append({"fen_key": position_key(board.fen()), "fen": board.fen(),
                              "route_json": json.dumps([*origin, *moves[:ply]]), "ply": absolute_ply,
                              "in_scope": int(absolute_ply >= len(prefix_moves))})
        if ply < len(moves):
            move = chess.Move.from_uci(moves[ply])
            if move not in board.legal_moves:
                return {"status": "conflict", "reason": f"Illegal move at offset {ply + 1}", "disagreement_ply": ply}
            board.push(move)
    return {"status": "valid", "reason": None, "disagreement_ply": None,
            "origin": origin, "scope_start_ply": max(0, len(prefix_moves) - len(origin or [])),
            "positions": positions}


def line_origin(database, preview_id: str | None, starting_fen: str) -> list[str] | None:
    if position_key(starting_fen) == position_key(chess.STARTING_FEN):
        return []
    if not preview_id:
        return None
    anchor = database.execute(
        "SELECT route_json FROM canonical_prefix_positions WHERE preview_id=? AND fen_key=? "
        "ORDER BY in_scope DESC,ply,route_json LIMIT 1", (preview_id, position_key(starting_fen)),
    ).fetchone()
    return json.loads(anchor["route_json"]) if anchor else None


def ensure_line_in_scope(database, repertoire_id: str, starting_fen: str,
                         moves: list[str], *, remember: bool = True) -> dict:
    prefix = read_prefix(database, repertoire_id, lock=True)
    if not prefix["moves"]:
        return {"scope_start_ply": 0, "origin": None}
    result = validate_scoped_line(starting_fen, moves, prefix["moves"],
                                  line_origin(database, prefix["preview_id"], starting_fen))
    if result["status"] != "valid":
        from fastapi import HTTPException
        raise HTTPException(409, "This line is outside the repertoire's canonical prefix. " + result["reason"])
    if remember and prefix["preview_id"]:
        store_positions(database, prefix["preview_id"], result["positions"])
    return result


def scope_line(database, repertoire_id: str, line: dict, prefix: dict | None = None) -> dict:
    prefix = prefix if prefix is not None else read_prefix(database, repertoire_id)
    if not prefix["moves"]:
        return {**line, "scope_start_ply": 0}
    origin = line_origin(database, prefix["preview_id"], line["start_fen"])
    # Stored lines passed the write boundary; this read only attaches their offset.
    if origin is None:
        return {**line, "scope_start_ply": len(json.loads(line["moves_json"])) + 1}
    return {**line, "scope_start_ply": max(0, len(prefix["moves"]) - len(origin))}


def scope_lines(database, repertoire_id: str, lines: list[dict]) -> list[dict]:
    prefix = read_prefix(database, repertoire_id)
    if not prefix["moves"]:
        return lines
    return [scope_line(database, repertoire_id, line, prefix) for line in lines]


def store_positions(database, preview_id: str, positions: list[dict]) -> None:
    if not positions:
        return
    if hasattr(database, "execute_native"):
        database.execute_native(
            "INSERT INTO canonical_prefix_positions(preview_id,fen_key,in_scope,fen,route_json,ply) "
            "SELECT %s,fen_key,in_scope,fen,route_json,ply FROM jsonb_to_recordset(%s::jsonb) "
            "AS position(fen_key text,in_scope bigint,fen text,route_json text,ply bigint) "
            "ON CONFLICT(preview_id,fen_key,in_scope) DO NOTHING",
            (preview_id, json.dumps(positions)),
        )
    else:
        database.executemany(
            "INSERT OR IGNORE INTO canonical_prefix_positions(preview_id,fen_key,in_scope,fen,route_json,ply) VALUES(?,?,?,?,?,?)",
            [(preview_id, item["fen_key"], item["in_scope"], item["fen"], item["route_json"], item["ply"]) for item in positions],
        )
