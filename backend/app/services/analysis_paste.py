"""Preview and atomically add pasted SAN/PGN variations to repertoires."""

from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

import chess
import chess.pgn

from .cards import card_id
from .pgn import ends_on_trained_move
from .repertoire_comparison import canonical_fen


MAX_PASTE_BYTES = 65_536
MAX_PASTE_LINES = 50
MAX_LINE_PLIES = 256
STANDARD_FEN_KEY = canonical_fen(chess.STARTING_FEN)


class PasteInputError(ValueError):
    pass


class StalePastePreview(ValueError):
    pass


@dataclass(frozen=True)
class PastedLine:
    starting_fen: str
    moves: tuple[str, ...]
    san: str


def _validate_movetext_tokens(source: str) -> None:
    """python-chess ignores unknown PGN words, so reject them explicitly."""
    without_headers = re.sub(r"(?m)^\s*\[[^\n]*\]\s*$", " ", source)
    without_comments = re.sub(r"\{[^}]*\}|;[^\n]*|(?m:^%[^\n]*)", " ", without_headers, flags=re.S)
    without_numbers = re.sub(r"\b\d+\.(?:\.\.)?|\.\.\.", " ", without_comments)
    without_nags = re.sub(r"\$\d+", " ", without_numbers)
    for token in re.sub(r"[()]", " ", without_nags).split():
        if token in {"*", "1-0", "0-1", "1/2-1/2"}:
            continue
        normalized = token.rstrip("!?")
        if not chess.SAN_REGEX.fullmatch(normalized) and not re.fullmatch(r"[O0]-[O0](?:-[O0])?[+#]?", normalized):
            raise PasteInputError(f"Unrecognized move text: {token}")


def _collect_variations(node: chess.pgn.GameNode, moves: tuple[str, ...], board: chess.Board) -> list[tuple[str, ...]]:
    if len(moves) > MAX_LINE_PLIES:
        raise PasteInputError(f"A pasted line may contain at most {MAX_LINE_PLIES} moves")
    if not node.variations:
        return [moves] if moves else []
    collected: list[tuple[str, ...]] = []
    for child in node.variations:
        if len(collected) >= MAX_PASTE_LINES:
            raise PasteInputError(f"Paste at most {MAX_PASTE_LINES} variations at a time")
        next_board = board.copy(stack=False)
        if child.move not in next_board.legal_moves:
            raise PasteInputError(f"Illegal move after {board.fullmove_number}: {child.san()}")
        next_board.push(child.move)
        collected.extend(_collect_variations(child, (*moves, child.move.uci()), next_board))
    return collected


def parse_pasted_lines(raw_text: str, starting_fen: str | None) -> list[PastedLine]:
    if not raw_text.strip():
        raise PasteInputError("Paste SAN moves or PGN first")
    if len(raw_text.encode("utf-8")) > MAX_PASTE_BYTES:
        raise PasteInputError("Paste is too large; use at most 64 KiB")
    context_fen = None
    if starting_fen:
        try:
            context_board = chess.Board(starting_fen)
            if not context_board.is_valid():
                raise ValueError
            context_fen = context_board.fen()
        except ValueError as error:
            raise PasteInputError("Starting FEN is invalid") from error
    has_headers = bool(re.search(r"(?m)^\s*\[[A-Za-z]+\s+\"", raw_text))
    chunks = [raw_text] if has_headers else re.split(r"\n\s*\n", raw_text.strip())
    parsed: list[PastedLine] = []
    for chunk in chunks:
        source = chunk
        if context_fen and not has_headers:
            source = f'[SetUp "1"]\n[FEN "{context_fen}"]\n\n{chunk}'
        _validate_movetext_tokens(source)
        stream = io.StringIO(source)
        while game := chess.pgn.read_game(stream):
            if game.errors:
                raise PasteInputError(f"Could not parse a move: {game.errors[0]}")
            board = game.board()
            if not board.is_valid():
                raise PasteInputError("PGN starting position is invalid")
            for moves in _collect_variations(game, (), board):
                if len(parsed) >= MAX_PASTE_LINES:
                    raise PasteInputError(f"Paste at most {MAX_PASTE_LINES} variations at a time")
                replay = board.copy(stack=False)
                san_moves: list[str] = []
                for move_uci in moves:
                    move = chess.Move.from_uci(move_uci)
                    san_moves.append(replay.san(move))
                    replay.push(move)
                parsed.append(PastedLine(board.fen(), moves, " ".join(san_moves)))
    if not parsed:
        raise PasteInputError("No playable moves were found")
    return parsed


def _position_moves(line: PastedLine) -> list[tuple[str, str]]:
    board = chess.Board(line.starting_fen)
    positions: list[tuple[str, str]] = []
    for move_uci in line.moves:
        positions.append((canonical_fen(board.fen()), move_uci))
        board.push_uci(move_uci)
    return positions


def _snapshot(database: sqlite3.Connection) -> tuple[list[dict], list[dict], str]:
    repertoires = [dict(row) for row in database.execute(
        """SELECT id,name FROM repertoires
           WHERE id NOT IN ('__tactics__','__endgames__','__game_mistakes__') ORDER BY id"""
    )]
    lines = [dict(row) for row in database.execute(
        "SELECT id,repertoire_id,trained_color,start_fen,moves_json FROM repertoire_lines ORDER BY id"
    )]
    signature = hashlib.sha256(json.dumps([repertoires, lines], sort_keys=True).encode()).hexdigest()
    return repertoires, lines, signature


def _destination_options(pasted: PastedLine, repertoires: list[dict], stored_lines: list[dict], parsed_existing: dict[str, list[tuple[str, list[tuple[str, str]], str]]]) -> list[dict]:
    pasted_edges = _position_moves(pasted)
    options: list[dict] = []
    for repertoire in repertoires:
        routes = parsed_existing.get(repertoire["id"], [])
        colors = {color for color, _, _ in routes}
        trained_color = next(iter(colors)) if len(colors) == 1 else None
        known_edges = {edge for _, edges, _ in routes for edge in edges}
        known_positions = {position for position, _ in known_edges} | {terminal for _, _, terminal in routes}
        score = sum(edge in known_edges for edge in pasted_edges)
        if score == 0 and canonical_fen(pasted.starting_fen) != STANDARD_FEN_KEY:
            score = int(canonical_fen(pasted.starting_fen) in known_positions)
        duplicate = any(
            stored["repertoire_id"] == repertoire["id"]
            and canonical_fen(stored["start_fen"]) == canonical_fen(pasted.starting_fen)
            and tuple(json.loads(stored["moves_json"])) == pasted.moves
            for stored in stored_lines
        )
        conflicts: list[dict] = []
        if trained_color:
            board = chess.Board(pasted.starting_fen)
            existing_responses: dict[str, set[str]] = {}
            for color, edges, _ in routes:
                if color != trained_color:
                    continue
                for position, move_uci in edges:
                    if position.split()[1] == ("w" if trained_color == "white" else "b"):
                        existing_responses.setdefault(position, set()).add(move_uci)
            for position, move_uci in pasted_edges:
                if board.turn == (trained_color == "white"):
                    alternatives = existing_responses.get(position, set()) - {move_uci}
                    if alternatives:
                        conflicts.append({"fen": position, "existing_moves": sorted(alternatives), "pasted_move": move_uci})
                board.push_uci(move_uci)
        options.append({
            "repertoire_id": repertoire["id"], "name": repertoire["name"],
            "trained_color": trained_color, "score": score,
            "duplicate": duplicate, "conflicts": conflicts,
            "trainable": bool(trained_color and ends_on_trained_move(pasted.starting_fen, list(pasted.moves), trained_color)),
        })
    for option in options:
        option["matched"] = option["score"] > 0
    return options


def build_paste_preview(
    database: sqlite3.Connection, raw_text: str, starting_fen: str | None,
    source_gap_id: str | None,
) -> dict:
    parsed = parse_pasted_lines(raw_text, starting_fen)
    repertoires, stored_lines, signature = _snapshot(database)
    if not repertoires:
        raise PasteInputError("Import a repertoire before pasting analysis")
    token = hashlib.sha256(json.dumps(
        [raw_text, starting_fen, source_gap_id, signature], sort_keys=True
    ).encode()).hexdigest()
    parsed_existing: dict[str, list[tuple[str, list[tuple[str, str]], str]]] = {}
    for stored in stored_lines:
        try:
            existing = PastedLine(stored["start_fen"], tuple(json.loads(stored["moves_json"])), "")
            terminal_board = chess.Board(existing.starting_fen)
            for move_uci in existing.moves:
                terminal_board.push_uci(move_uci)
            parsed_existing.setdefault(stored["repertoire_id"], []).append(
                (stored["trained_color"], _position_moves(existing), canonical_fen(terminal_board.fen()))
            )
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    lines = []
    for index, pasted in enumerate(parsed):
        options = _destination_options(pasted, repertoires, stored_lines, parsed_existing)
        matches = [option for option in options if option["matched"]]
        lines.append({
            "index": index, "starting_fen": pasted.starting_fen,
            "san": pasted.san, "moves": list(pasted.moves),
            "suggested_repertoire_id": matches[0]["repertoire_id"] if len(matches) == 1 else None,
            "options": options,
        })
    return {"preview_token": token, "lines": lines}


def _resolves_gap(database: sqlite3.Connection, source_gap_id: str, repertoire_id: str, pasted: PastedLine) -> bool:
    try:
        node_id, opponent_move = source_gap_id.split(":", 1)
    except ValueError as error:
        raise PasteInputError("Invalid coverage gap") from error
    gap = database.execute(
        """SELECT n.fen,n.repertoire_id FROM repertoire_coverage_candidates c
           JOIN repertoire_coverage_nodes n ON n.id=c.node_id
           WHERE c.node_id=? AND c.move_uci=?""", (node_id, opponent_move)
    ).fetchone()
    if not gap or gap["repertoire_id"] != repertoire_id:
        return False
    gap_fen_key = canonical_fen(gap["fen"])
    for position_index, (position, move) in enumerate(_position_moves(pasted)):
        if position == gap_fen_key and move == opponent_move and position_index + 1 < len(pasted.moves):
            return True
    return False


def commit_pasted_lines(
    database: sqlite3.Connection, raw_text: str, starting_fen: str | None,
    source_gap_id: str | None, preview_token: str, selections: list[dict],
    prepared_preview: dict, parsed: list[PastedLine],
) -> dict:
    if prepared_preview["preview_token"] != preview_token:
        raise StalePastePreview("Repertoires changed since the preview. Preview again before saving")
    _, _, current_signature = _snapshot(database)
    current_token = hashlib.sha256(json.dumps(
        [raw_text, starting_fen, source_gap_id, current_signature], sort_keys=True
    ).encode()).hexdigest()
    if current_token != preview_token:
        raise StalePastePreview("Repertoires changed since the preview. Preview again before saving")
    by_index = {item["index"]: item for item in selections}
    if len(by_index) != len(selections) or not selections:
        raise PasteInputError("Choose at least one unique line to save")
    batch_responses: dict[tuple[str, str], tuple[str, int]] = {}
    batch_conflict_indices: set[int] = set()
    for index, selected in sorted(by_index.items()):
        if not isinstance(index, int) or index < 0 or index >= len(parsed):
            raise PasteInputError("Invalid pasted line selection")
        option = next((value for value in prepared_preview["lines"][index]["options"] if value["repertoire_id"] == selected["repertoire_id"]), None)
        if not option or option["trained_color"] not in ("white", "black"):
            raise PasteInputError("Selected repertoire has no defined training side")
        trained_turn = "w" if option["trained_color"] == "white" else "b"
        for position, move in _position_moves(parsed[index]):
            if position.split()[1] != trained_turn:
                continue
            key = (selected["repertoire_id"], position)
            previous = batch_responses.get(key)
            if previous and previous[0] != move:
                batch_conflict_indices.update((index, previous[1]))
            else:
                batch_responses[key] = (move, index)
    if any(not by_index[index].get("acknowledge_conflict") for index in batch_conflict_indices):
        raise PasteInputError("Confirm conflicting trained moves between pasted lines before saving")
    now = datetime.now(timezone.utc).isoformat()
    saved: list[dict] = []
    affected: set[str] = set()
    resolved_gap = False
    inserted_keys: set[tuple[str, str, tuple[str, ...]]] = set()
    for index, selected in sorted(by_index.items()):
        line = prepared_preview["lines"][index]
        option = next((value for value in line["options"] if value["repertoire_id"] == selected["repertoire_id"]), None)
        if not option:
            raise PasteInputError("Selected repertoire is unavailable")
        if option["conflicts"] and not selected.get("acknowledge_conflict"):
            raise PasteInputError("Confirm the trained-move conflict before saving")
        if option["trained_color"] not in ("white", "black"):
            raise PasteInputError("Selected repertoire has no defined training side")
        if not option["trainable"]:
            raise PasteInputError("Each saved line must finish with a move by the side being trained")
        pasted = parsed[index]
        line_id = hashlib.sha256(
            f"{selected['repertoire_id']}\0{card_id(pasted.starting_fen, list(pasted.moves))}".encode()
        ).hexdigest()
        insert_key = (selected["repertoire_id"], canonical_fen(pasted.starting_fen), pasted.moves)
        duplicate = option["duplicate"] or insert_key in inserted_keys
        inserted_keys.add(insert_key)
        if not duplicate:
            database.execute(
                """INSERT OR IGNORE INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (line_id, selected["repertoire_id"], pasted.san,
                 option["trained_color"], pasted.starting_fen, json.dumps(list(pasted.moves)), now),
            )
            depth = database.execute("SELECT initial_depth FROM settings WHERE id=1").fetchone()[0]
            database.execute(
                "INSERT OR IGNORE INTO repertoire_line_training_depths(line_id,learner_decision_count) VALUES(?,?)",
                (line_id, depth),
            )
            affected.add(selected["repertoire_id"])
        if source_gap_id and _resolves_gap(database, source_gap_id, selected["repertoire_id"], pasted):
            node_id, opponent_move = source_gap_id.split(":", 1)
            database.execute(
                "UPDATE repertoire_coverage_candidates SET covered=1 WHERE node_id=? AND move_uci=?",
                (node_id, opponent_move),
            )
            resolved_gap = True
            affected.add(selected["repertoire_id"])
        saved.append({"index": index, "repertoire_id": selected["repertoire_id"],
                      "duplicate": duplicate, "conflict": bool(option["conflicts"]) or index in batch_conflict_indices})
    return {"saved": saved, "affected_repertoire_ids": sorted(affected), "gap_resolved": resolved_gap}
