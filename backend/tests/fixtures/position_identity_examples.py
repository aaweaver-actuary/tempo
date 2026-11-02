"""Small, legal chess paths for the position identity contract tests.

These are synthetic route examples, not certificates about a live repertoire.
Each position is derived by python-chess replay so the examples cannot drift
from their UCI paths.
"""
from __future__ import annotations

from dataclasses import dataclass

import chess
from tempo_evidence_math import opening_position_key


@dataclass(frozen=True)
class LegalPath:
    repertoire_id: str
    name: str
    moves_uci: tuple[str, ...]
    start_fen: str = chess.STARTING_FEN

    def board_at(self, ply: int) -> chess.Board:
        if not 0 <= ply <= len(self.moves_uci):
            raise IndexError("ply_outside_path")
        board = chess.Board(self.start_fen)
        for move_uci in self.moves_uci[:ply]:
            move = chess.Move.from_uci(move_uci)
            if move not in board.legal_moves:
                raise ValueError(f"illegal_fixture_move:{self.name}:{move_uci}")
            board.push(move)
        return board

    def position(self, ply: int) -> str:
        return opening_position_key(self.board_at(ply).fen())


# Shared Italian trunk, then two legal black continuations: the Knight Defence
# and Giuoco Piano. The first five plies are identical.
ITALIAN_KNIGHT = LegalPath(
    "italian", "italian_knight",
    ("e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6", "d2d3"),
)
ITALIAN_GIUOCO = LegalPath(
    "italian", "italian_giuoco",
    ("e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5", "d2d3"),
)

# Same London position reached through different exact move orders. Both paths
# end after ...e6, with White to move and no legal en-passant capture.
LONDON_ORDER_A = LegalPath(
    "london", "london_d4_d5_bf4_nf6",
    ("d2d4", "d7d5", "c1f4", "g8f6", "e2e3", "e7e6"),
)
LONDON_ORDER_B = LegalPath(
    "london", "london_d4_nf6_bf4_d5",
    ("d2d4", "g8f6", "c1f4", "d7d5", "e2e3", "e7e6"),
)

# This legal synthetic root has the starting placement and turn without
# castling rights. It isolates the rights field as part of position identity.
CASTLING_INTACT = LegalPath("identity", "castling_intact", ())
CASTLING_LOST = LegalPath(
    "identity", "castling_lost", (),
    start_fen=chess.STARTING_FEN.replace("KQkq", "-"),
)

# Legal en-passant target after 1.e4 a6 2.e5 d5; the capture e5xd6 is legal.
LEGAL_EP = LegalPath("identity", "legal_ep", ("e2e4", "a7a6", "e4e5", "d7d5"))

# After 1.e4, the syntactic target e3 cannot be captured. The canonical helper
# intentionally normalizes it to the same identity as the ordinary FEN.
IRRELEVANT_EP = LegalPath("identity", "irrelevant_ep", ("e2e4",))


def with_irrelevant_ep(path: LegalPath, ply: int) -> str:
    board = path.board_at(ply)
    raw = board.fen(en_passant="fen")
    fields = raw.split()
    assert fields[3] != "-"
    fields[3] = "-"
    return " ".join(fields)


def invalid_fen() -> str:
    return "not a valid FEN"
