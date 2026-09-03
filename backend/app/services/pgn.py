import io
from dataclasses import dataclass

import chess
import chess.pgn


@dataclass(frozen=True)
class ParsedLine:
    starting_fen: str
    moves: list[str]


def _collect_lines(node: chess.pgn.GameNode, board: chess.Board, moves: list[str]) -> list[list[str]]:
    if not node.variations:
        return [moves]
    lines: list[list[str]] = []
    for variation in node.variations:
        next_board = board.copy()
        uci = variation.move.uci()
        next_board.push(variation.move)
        lines.extend(_collect_lines(variation, next_board, [*moves, uci]))
    return lines


def parse_pgn(raw_pgn: str) -> tuple[int, list[ParsedLine]]:
    stream = io.StringIO(raw_pgn)
    games = 0
    parsed: list[ParsedLine] = []
    while game := chess.pgn.read_game(stream):
        games += 1
        board = game.board()
        starting_fen = board.fen()
        for moves in _collect_lines(game, board, []):
            if moves:
                parsed.append(ParsedLine(starting_fen=starting_fen, moves=moves))
    return games, parsed


def prefix_through_user_moves(
    starting_fen: str,
    moves_uci: list[str],
    trained_color: str,
    user_move_count: int,
) -> list[str]:
    """Include replies as needed until the configured number of user moves is reached."""
    board = chess.Board(starting_fen)
    target_color = chess.WHITE if trained_color == "white" else chess.BLACK
    prefix: list[str] = []
    moves_seen = 0
    for uci in moves_uci:
        moving_color = board.turn
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            break
        prefix.append(uci)
        board.push(move)
        if moving_color == target_color:
            moves_seen += 1
            if moves_seen == user_move_count:
                break
    return prefix
