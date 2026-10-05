"""Explicit opening scope, independent of scheduled opening prefix cards."""

from __future__ import annotations

import json
import re
import sqlite3

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


def assumed_position_keys(prefix_moves: list[str]) -> set[str]:
    """Assumed positions come from current opening metadata, not source certificates."""
    board = chess.Board()
    positions = set()
    for move_uci in prefix_moves:
        positions.add(position_key(board.fen()))
        board.push_uci(move_uci)
    return positions


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
        "SELECT position.route_json FROM canonical_prefix_positions position "
        "JOIN canonical_prefix_previews preview ON preview.id=position.preview_id "
        "JOIN repertoires repertoire ON repertoire.id=preview.repertoire_id "
        "WHERE position.preview_id=? AND position.fen_key=? "
        "AND position.source_revision=repertoire.scope_source_revision "
        "ORDER BY position.in_scope DESC,position.ply,position.route_json LIMIT 1",
        (preview_id, position_key(starting_fen)),
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
        raise HTTPException(409, "This line is outside the repertoire's canonical prefix. " + result["reason"]
                            + ". Check the canonical prefix again to verify current routes.")
    if remember and prefix["preview_id"]:
        store_positions(database, prefix["preview_id"], result["positions"], source_revision=prefix["source_revision"])
    return result


def certify_admitted_route(database, repertoire_id: str, validation_result: dict) -> None:
    """Certify only a route validated by this transaction, after its final source write."""
    if not validation_result.get("positions"):
        return
    current = read_prefix(database, repertoire_id, lock=True)
    if current["moves"] and current["preview_id"]:
        store_positions(database, current["preview_id"], validation_result["positions"],
                        source_revision=current["source_revision"])


def position_rank(position: dict) -> tuple[int, int, str]:
    """Prefer an in-scope, shortest, then lexically stable verified origin."""
    return (-position["in_scope"], position["ply"], position["route_json"])


def ensure_batch_lines_in_scope(database, candidates: list[tuple[str, str, list[str]]]) -> list[dict]:
    """Resolve selected routes together without persisting validation certificates."""
    if isinstance(database, sqlite3.Connection) and not database.in_transaction:
        database.execute("BEGIN IMMEDIATE")
    prefixes = {repertoire_id: read_prefix(database, repertoire_id, lock=True)
                for repertoire_id in sorted({candidate[0] for candidate in candidates})}
    local_positions: dict[str, dict[str, dict]] = {repertoire_id: {} for repertoire_id in prefixes}
    certified_origins: dict[tuple[str, str], list[str] | None] = {}
    validated_routes: dict[int, dict] = {}
    ordered_indices = sorted(range(len(candidates)), key=lambda index: (
        candidates[index][0], position_key(candidates[index][1]), tuple(candidates[index][2])))

    while True:
        unresolved_indices = []
        failed_routes = {}
        origins_improved = False
        for candidate_index in ordered_indices:
            repertoire_id, starting_fen, moves = candidates[candidate_index]
            prefix = prefixes[repertoire_id]
            if not prefix["moves"]:
                validated_routes[candidate_index] = {"scope_start_ply": 0, "origin": None}
                continue
            starting_key = position_key(starting_fen)
            if starting_key == position_key(chess.STARTING_FEN):
                origin = []
            else:
                certificate_key = (repertoire_id, starting_key)
                if certificate_key not in certified_origins:
                    certified_origins[certificate_key] = line_origin(database, prefix["preview_id"], starting_fen)
                available_origins = []
                certified_origin = certified_origins[certificate_key]
                if certified_origin is not None:
                    available_origins.append({"in_scope": int(len(certified_origin) >= len(prefix["moves"])),
                                              "ply": len(certified_origin), "route_json": json.dumps(certified_origin)})
                local_origin = local_positions[repertoire_id].get(starting_key)
                if local_origin is not None:
                    available_origins.append(local_origin)
                origin = json.loads(min(available_origins, key=position_rank)["route_json"]) if available_origins else None
            validation = validate_scoped_line(starting_fen, moves, prefix["moves"], origin)
            if validation["status"] != "valid":
                unresolved_indices.append(candidate_index)
                failed_routes[candidate_index] = validation
                continue
            validated_routes[candidate_index] = validation
            for position in validation["positions"]:
                previous = local_positions[repertoire_id].get(position["fen_key"])
                if previous is None or position_rank(position) < position_rank(previous):
                    local_positions[repertoire_id][position["fen_key"]] = position
                    origins_improved = True
        # Already valid continuations may have used an older, longer origin.
        # Revisit them as well until every selected route uses the best closure.
        if origins_improved:
            continue
        if unresolved_indices:
            from fastapi import HTTPException
            failure = failed_routes[unresolved_indices[0]]
            raise HTTPException(409, "This line is outside the repertoire's canonical prefix. " + failure["reason"]
                                + ". Check the canonical prefix again to verify current routes.")
        break
    return [validated_routes[index] for index in range(len(candidates))]


def scope_line(database, repertoire_id: str, line: dict, prefix: dict | None = None) -> dict:
    """Build an analysis route without changing the saved training start or moves."""
    prefix = prefix if prefix is not None else read_prefix(database, repertoire_id)
    if not prefix["moves"]:
        return {**line, "scope_start_ply": 0}
    origin = line_origin(database, prefix["preview_id"], line["start_fen"])
    # Stored lines passed the write boundary. Reconstruct their verified origin so
    # downstream opponent moves retain their probabilities and absolute horizon.
    if origin is None:
        return {**line, "scope_start_ply": len(json.loads(line["moves_json"])) + 1, "scope_pending": True}
    return {**line, "start_fen": chess.STARTING_FEN,
            "moves_json": json.dumps([*origin, *json.loads(line["moves_json"])]),
            "scope_start_ply": len(prefix["moves"])}


def scope_lines(database, repertoire_id: str, lines: list[dict]) -> list[dict]:
    prefix = read_prefix(database, repertoire_id)
    if not prefix["moves"]:
        return lines
    return [scope_line(database, repertoire_id, line, prefix) for line in lines]


def store_positions(database, preview_id: str, positions: list[dict], *, source_revision: int | None = None) -> None:
    if not positions:
        return
    if source_revision is None:
        source_revision = database.execute("SELECT source_revision FROM canonical_prefix_previews WHERE id=?", (preview_id,)).fetchone()[0]
    best_positions: dict[tuple[str, int], dict] = {}
    for position in positions:
        key = (position["fen_key"], position["in_scope"])
        previous = best_positions.get(key)
        if previous is None or position_rank(position) < position_rank(previous):
            best_positions[key] = position
    positions = [best_positions[key] for key in sorted(best_positions)]
    # Rank only within one verified source epoch. Old short routes must not
    # prevent recertification, and delayed old writes cannot renew stale proof.
    replacement_guard = (
        " WHERE excluded.source_revision>canonical_prefix_positions.source_revision OR "
        "(excluded.source_revision=canonical_prefix_positions.source_revision AND "
        "(excluded.ply,excluded.route_json)<(canonical_prefix_positions.ply,canonical_prefix_positions.route_json))"
    )
    if hasattr(database, "execute_native"):
        database.execute_native(
            "INSERT INTO canonical_prefix_positions(preview_id,fen_key,in_scope,fen,route_json,ply,source_revision) "
            "SELECT %s,fen_key,in_scope,fen,route_json,ply,%s FROM jsonb_to_recordset(%s::jsonb) "
            "AS position(fen_key text,in_scope bigint,fen text,route_json text,ply bigint) "
            "ON CONFLICT(preview_id,fen_key,in_scope) DO UPDATE SET fen=excluded.fen,route_json=excluded.route_json,"
            "ply=excluded.ply,source_revision=excluded.source_revision" + replacement_guard,
            (preview_id, source_revision, json.dumps(positions)),
        )
    else:
        database.executemany(
            "INSERT INTO canonical_prefix_positions(preview_id,fen_key,in_scope,fen,route_json,ply,source_revision) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(preview_id,fen_key,in_scope) DO UPDATE SET fen=excluded.fen,route_json=excluded.route_json,"
            "ply=excluded.ply,source_revision=excluded.source_revision" + replacement_guard,
            [(preview_id, item["fen_key"], item["in_scope"], item["fen"], item["route_json"], item["ply"], source_revision) for item in positions],
        )
