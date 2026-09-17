import io
import re
from dataclasses import dataclass

import chess
import chess.pgn


@dataclass(frozen=True)
class ParsedLine:
    starting_fen: str
    moves: list[str]
    annotations: list["ParsedAnnotation"]


@dataclass(frozen=True)
class ParsedAnnotation:
    fen_key: str
    comment: str
    arrows: list[dict[str, str]]
    squares: list[dict[str, str]]


_GRAPHICS = re.compile(r"\[%c(?P<kind>al|sl)\s+(?P<values>[^\]]+)\]", re.IGNORECASE)
_COLORS = {"G": "green", "R": "red", "B": "blue", "Y": "yellow"}


def _annotation(node: chess.pgn.GameNode, board: chess.Board) -> ParsedAnnotation | None:
    raw = node.comment or ""
    arrows: list[dict[str, str]] = []
    squares: list[dict[str, str]] = []
    for match in _GRAPHICS.finditer(raw):
        for value in match.group("values").split(","):
            token = value.strip()
            color = _COLORS.get(token[:1].upper(), "green")
            if match.group("kind").lower() == "al" and len(token) == 5:
                arrows.append({"from": token[1:3].lower(), "to": token[3:5].lower(), "color": color})
            elif match.group("kind").lower() == "sl" and len(token) == 3:
                squares.append({"square": token[1:3].lower(), "color": color})
    comment = _GRAPHICS.sub("", raw).strip()
    if not comment and not arrows and not squares:
        return None
    return ParsedAnnotation(
        fen_key=" ".join(board.fen().split()[:4]),
        comment=comment,
        arrows=arrows,
        squares=squares,
    )


def _collect_lines(node: chess.pgn.GameNode, board: chess.Board, moves: list[str], annotations: list[ParsedAnnotation]) -> list[tuple[list[str], list[ParsedAnnotation]]]:
    here = _annotation(node, board)
    current_annotations = [*annotations, *([here] if here else [])]
    if not node.variations:
        return [(moves, current_annotations)]
    lines: list[tuple[list[str], list[ParsedAnnotation]]] = []
    for variation in node.variations:
        next_board = board.copy()
        uci = variation.move.uci()
        next_board.push(variation.move)
        lines.extend(_collect_lines(variation, next_board, [*moves, uci], current_annotations))
    return lines


def parse_pgn(raw_pgn: str) -> tuple[int, list[ParsedLine]]:
    stream = io.StringIO(raw_pgn)
    games = 0
    parsed: list[ParsedLine] = []
    while game := chess.pgn.read_game(stream):
        games += 1
        board = game.board()
        starting_fen = board.fen()
        for moves, annotations in _collect_lines(game, board, [], []):
            if moves:
                parsed.append(ParsedLine(starting_fen=starting_fen, moves=moves, annotations=annotations))
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
    last_moving_color: chess.Color | None = None
    for uci in moves_uci:
        moving_color = board.turn
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            break
        prefix.append(uci)
        last_moving_color = moving_color
        board.push(move)
        if moving_color == target_color:
            moves_seen += 1
            if moves_seen == user_move_count:
                break
    # A card must finish with a move by the side being trained.  Incomplete
    # Black data such as ``1. e4`` is useful PGN, but is not a playable Black
    # card: auto-playing e4 would leave no response for the learner.
    if not prefix or moves_seen == 0:
        return []
    return prefix if last_moving_color == target_color else []


def ends_on_trained_move(
    starting_fen: str,
    moves_uci: list[str],
    trained_color: str,
) -> bool:
    """Return whether a stored opening line is legal and ends on its learner."""
    if trained_color not in {"white", "black"} or not moves_uci:
        return False
    try:
        board = chess.Board(starting_fen)
    except ValueError:
        return False
    target_color = chess.WHITE if trained_color == "white" else chess.BLACK
    last_moving_color: chess.Color | None = None
    for uci in moves_uci:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            return False
        if move not in board.legal_moves:
            return False
        last_moving_color = board.turn
        board.push(move)
    return last_moving_color == target_color
