"""Pure comparison of normalized game and authored repertoire facts."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, TypedDict

import chess
from tempo_chess_core import canonical_fen_key


@dataclass(frozen=True)
class GameFacts:
    start_fen: str
    moves: tuple[str, ...]


@dataclass(frozen=True)
class AuthoredLine:
    start_fen: str
    moves: tuple[str, ...]


class Unavailable(TypedDict):
    kind: Literal["unavailable"]
    reason: str


class CoverageEnd(TypedDict):
    kind: Literal["coverage_end"]
    ply: int
    fen: str


class Departure(TypedDict):
    kind: Literal["departure"]
    ply: int
    fen: str
    played_uci: str
    expected_uci: list[str]
    actor: Literal["white", "black"]


class Covered(TypedDict):
    kind: Literal["covered"]


ComparisonResult = Unavailable | CoverageEnd | Departure | Covered


def position_identity(fen: str) -> str:
    return canonical_fen_key(chess.Board(fen).fen(en_passant="legal"))


def first_departure(game: GameFacts, lines: Sequence[AuthoredLine]) -> ComparisonResult:
    """Follow all alternatives from the exact start, never invent coverage.

    A route ending is explicit coverage end. A shorter alternative does not
    conceal a departure if another authored continuation remains available.
    """
    board = chess.Board(game.start_fen)
    identity = position_identity(game.start_fen)
    candidates = [
        line.moves for line in lines if position_identity(line.start_fen) == identity
    ]
    if not candidates:
        return {
            "kind": "unavailable",
            "reason": "No applicable authored line begins at this position",
        }
    for index, uci in enumerate(game.moves):
        expected = sorted({route[index] for route in candidates if len(route) > index})
        if not expected:
            return {"kind": "coverage_end", "ply": index + 1, "fen": board.fen()}
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            raise ValueError(f"Illegal game move at ply {index + 1}")
        if uci not in expected:
            return {
                "kind": "departure",
                "ply": index + 1,
                "fen": board.fen(),
                "played_uci": uci,
                "expected_uci": expected,
                "actor": "white" if board.turn else "black",
            }
        candidates = [
            route for route in candidates if len(route) > index and route[index] == uci
        ]
        board.push(move)
    return {"kind": "covered"}
