"""Strict PGN input and linear legal replay, independent of persistence."""
from datetime import datetime, timezone
import hashlib
import io
import json

import chess
import chess.pgn
from tempo_chess_core import canonical_fen_key

INDEX_VERSION = 1
MAX_SOURCE_BYTES = 8 * 1024 * 1024


def identity(*parts):
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


def utc_now():
    return datetime.now(timezone.utc)


def normalize_pgn(raw: bytes, player: str, *, played_at=None):
    if not player.strip() or len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("explicit_player_and_bounded_source_required")
    stream = io.StringIO(raw.decode("utf-8-sig"))
    game = chess.pgn.read_game(stream)
    if game is None or game.errors:
        raise ValueError("invalid_or_illegal_pgn")
    if chess.pgn.read_game(stream) is not None:
        raise ValueError("one_game_per_source_required")
    if game.headers.get("Variant", "Standard").casefold() not in {"standard", "from position"}:
        raise ValueError("unsupported_variant")
    matches = [color for color in ("white", "black")
               if game.headers.get(color.title(), "").casefold() == player.casefold()]
    if len(matches) != 1:
        raise ValueError("observed_player_missing_or_ambiguous")
    if game.headers.get("Result") not in {"1-0", "0-1", "1/2-1/2"}:
        raise ValueError("unfinished_or_unknown_result")
    board = game.board()
    if not board.is_valid():
        raise ValueError("invalid_starting_position")
    start_fen = board.fen(en_passant="fen")
    moves = []
    for move in game.mainline_moves():
        if move not in board.legal_moves:
            raise ValueError("illegal_move")
        moves.append(move.uci())
        board.push(move)
        if len(moves) > 1000:
            raise ValueError("game_exceeds_contract_move_bound")
    # A date without a time is not a known midnight game timestamp.
    if played_at is None and game.headers.get("UTCDate") and game.headers.get("UTCTime"):
        try:
            played_at = datetime.fromisoformat(game.headers["UTCDate"].replace(".", "-")
                                              + "T" + game.headers["UTCTime"] + "+00:00")
        except ValueError:
            played_at = None
    if played_at is not None:
        played_at = datetime.fromisoformat(str(played_at)) if isinstance(played_at, str) else played_at
        if played_at.tzinfo is None:
            raise ValueError("game_timestamp_requires_timezone")
        played_at = played_at.astimezone(timezone.utc).isoformat()
    return {"starting_fen": start_fen, "moves": moves, "observed_color": matches[0],
            "player": player.casefold(), "played_at": played_at,
            "source_digest": hashlib.sha256(raw).hexdigest(), "headers": dict(game.headers)}


def replay_positions(game):
    """Index every position including the terminal board, preserving FEN rights."""
    board = chess.Board(game["starting_fen"])
    positions = []
    for ply in range(len(game["moves"]) + 1):
        fen = board.fen(en_passant="fen")
        move = game["moves"][ply] if ply < len(game["moves"]) else None
        positions.append({"ply": ply, "fen": fen, "position_key": canonical_fen_key(fen),
                          "comparison_key": canonical_fen_key(board.fen(en_passant="legal")),
                          "turn": "white" if board.turn else "black", "move": move})
        if move is not None:
            parsed = chess.Move.from_uci(move)
            if parsed not in board.legal_moves:
                raise ValueError("illegal_index_source")
            board.push(parsed)
    return positions
