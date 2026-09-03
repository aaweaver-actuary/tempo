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
        san = next_board.san(variation.move)
        next_board.push(variation.move)
        lines.extend(_collect_lines(variation, next_board, [*moves, san]))
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
