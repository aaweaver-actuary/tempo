from __future__ import annotations
import chess
from .threat_models import ForkGeometry, ForkTarget


def _color_name(color: chess.Color) -> str:
    return "white" if color == chess.WHITE else "black"


def defensive_fork_geometry(
    position: chess.Board, move_uci: str
) -> ForkGeometry | None:
    """Return king-and-major geometry only for a legal knight move."""

    try:
        move = chess.Move.from_uci(move_uci)
    except ValueError as error:
        raise ValueError("Malformed knight move") from error
    if move not in position.legal_moves:
        raise ValueError("Illegal knight move")
    moving_piece = position.piece_at(move.from_square)
    if moving_piece is None or moving_piece.piece_type != chess.KNIGHT:
        return None
    after = position.copy(stack=False)
    after.push(move)
    attacked = after.attacks(move.to_square)
    enemy_color = not moving_piece.color
    king_square = after.king(enemy_color)
    if king_square is None or king_square not in attacked:
        return None
    major_targets = sorted(
        square
        for square in attacked
        if (piece := after.piece_at(square)) is not None
        and piece.color == enemy_color
        and piece.piece_type in (chess.ROOK, chess.QUEEN)
    )
    if not major_targets:
        return None
    major_square = major_targets[0]
    major_piece = after.piece_at(major_square)
    assert major_piece is not None
    return ForkGeometry(
        attacker_color=_color_name(moving_piece.color),
        knight_from=chess.square_name(move.from_square),
        knight_to=chess.square_name(move.to_square),
        king=ForkTarget(chess.square_name(king_square), "king"),
        major=ForkTarget(
            chess.square_name(major_square),
            "queen" if major_piece.piece_type == chess.QUEEN else "rook",
        ),
        other_majors=tuple(
            ForkTarget(
                chess.square_name(square),
                "queen" if after.piece_at(square).piece_type == chess.QUEEN else "rook",
            )
            for square in major_targets[1:]
        ),
    )
