"""Preview PGN as source records and occurrence trees without making cards."""

from __future__ import annotations

import hashlib
import io

import chess
import chess.pgn


MAX_RECORDS = 300
MAX_NODES = 20_000
MAX_DEPTH = 80
MAX_BRANCH_DEPTH = 16


def _annotations(node: chess.pgn.GameNode) -> tuple[list[dict], list[dict]]:
    arrows = []
    squares = []
    for arrow in node.arrows():
        color = arrow.color
        if arrow.tail == arrow.head:
            squares.append({"square": chess.square_name(arrow.head), "color": color})
        else:
            arrows.append({"from": chess.square_name(arrow.tail), "to": chess.square_name(arrow.head), "color": color})
    return arrows, squares


def preview_pgn(raw_pgn: str) -> dict:
    if len(raw_pgn.encode("utf-8")) > 2_000_000:
        raise ValueError("PGN exceeds the 2 MB study import limit")
    stream = io.StringIO(raw_pgn)
    records = []
    total_nodes = 0
    while True:
        start = stream.tell()
        try:
            game = chess.pgn.read_game(stream)
        except Exception as error:
            records.append({"index": len(records), "raw_pgn": raw_pgn[start:], "headers": {},
                            "nodes": [], "diagnostics": [f"PGN parser stopped: {error}"], "valid": False})
            break
        if game is None:
            break
        if len(records) >= MAX_RECORDS:
            raise ValueError("PGN contains more than 300 records")
        end = stream.tell()
        diagnostics = [str(error) for error in game.errors]
        nodes = []
        try:
            root_board = game.board()
            if not root_board.is_valid():
                diagnostics.append("Root FEN is not a valid chess position")
            stack = [(game, root_board, None, "root", [], 0, 0, 0)]
            while stack:
                node, board, parent_path, path, history, child_index, depth, branch_depth = stack.pop()
                total_nodes += 1
                if total_nodes > MAX_NODES or depth > MAX_DEPTH or branch_depth > MAX_BRANCH_DEPTH:
                    raise ValueError("PGN exceeds the node or variation-depth limit")
                arrows, squares = _annotations(node)
                nodes.append({
                    "path": path, "parent_path": parent_path, "child_index": child_index,
                    "move_uci": node.move.uci() if node.move else None,
                    "fen": board.fen(), "history": history,
                    "comment": node.comment or "", "starting_comment": getattr(node, "starting_comment", "") or "",
                    "nags": sorted(node.nags), "arrows": arrows, "squares": squares,
                })
                for index in reversed(range(len(node.variations))):
                    child = node.variations[index]
                    child_board = board.copy(stack=True)
                    if child.move not in child_board.legal_moves:
                        diagnostics.append(f"Illegal move at {path}.{index}: {child.move.uci()}")
                        continue
                    child_board.push(child.move)
                    stack.append((child, child_board, path, f"{path}.{index}",
                                  [*history, child.move.uci()], index, depth + 1,
                                  branch_depth + (1 if index else 0)))
        except (ValueError, AssertionError) as error:
            diagnostics.append(str(error))
        records.append({"index": len(records), "raw_pgn": raw_pgn[start:end],
                        "headers": dict(game.headers), "nodes": nodes,
                        "diagnostics": diagnostics, "valid": not diagnostics and bool(nodes)})
    if not records:
        raise ValueError("No PGN records found")
    return {"digest": hashlib.sha256(raw_pgn.encode("utf-8")).hexdigest(),
            "records": records, "record_count": len(records)}
