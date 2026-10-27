"""Small, explicit standard-chess features; similarity never establishes identity."""
from dataclasses import dataclass

import chess


@dataclass(frozen=True)
class PositionFeatures:
    material: tuple[int, ...]
    pawns: tuple[int, int]  # black, white
    pieces: tuple[int, ...]  # black then white: N, B, R, Q, K
    castling: str
    en_passant: str | None
    legal_en_passant: bool
    in_check: bool
    king_attackers: tuple[str, ...]


@dataclass(frozen=True)
class Comparison:
    score: int  # 0..1000; a feature overlap, never a probability or evaluation
    similarities: tuple[str, ...]
    differences: tuple[str, ...]


def features(board: chess.Board) -> PositionFeatures:
    colors = (chess.BLACK, chess.WHITE)
    king = board.king(board.turn)
    return PositionFeatures(
        tuple(len(board.pieces(piece, color)) for color in colors for piece in chess.PIECE_TYPES),
        tuple(board.pawns & board.occupied_co[color] for color in colors),
        tuple(board.pieces_mask(piece, color) for color in colors
              for piece in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN, chess.KING)),
        board.castling_xfen(), chess.square_name(board.ep_square) if board.ep_square is not None else None,
        board.has_legal_en_passant(), board.is_check(),
        tuple(chess.square_name(square) for square in board.attackers(not board.turn, king))
        if king is not None else ())


def _overlap(first: tuple[int, ...], second: tuple[int, ...]) -> int:
    return sum((left & right).bit_count() for left, right in zip(first, second))


def compare(target: chess.Board, source: chess.Board, learner_color: str) -> Comparison | None:
    """Same material, learner pawns, >=75% enemy pawns and >=50% other pieces.

    These are deliberately conservative engineering defaults, not learned chess
    knowledge. Castling/EP and tactical differences remain visible, not erased.
    """
    target_features, source_features = features(target), features(source)
    side = int(learner_color == "white")
    if (target.turn != source.turn or target_features.material != source_features.material
            or target_features.pawns[side] != source_features.pawns[side]):
        return None
    enemy_count = target_features.pawns[1 - side].bit_count()
    enemy_overlap = (target_features.pawns[1 - side] & source_features.pawns[1 - side]).bit_count()
    piece_count = sum(value.bit_count() for value in target_features.pieces)
    piece_overlap = _overlap(target_features.pieces, source_features.pieces)
    if enemy_overlap * 4 < enemy_count * 3 or piece_overlap * 2 < piece_count:
        return None
    pawn_count = sum(value.bit_count() for value in target_features.pawns)
    pawn_overlap = _overlap(target_features.pawns, source_features.pawns)
    score = (600 * pawn_overlap // pawn_count if pawn_count else 600)
    score += 400 * piece_overlap // piece_count if piece_count else 400
    similarities = ("same side to move and colored material counts", "same learner pawn squares",
                    f"{pawn_overlap}/{pawn_count} pawns and {piece_overlap}/{piece_count} non-pawns on matching squares")
    differences = []
    for square in chess.SQUARES:
        before, after = source.piece_at(square), target.piece_at(square)
        if before != after:
            differences.append(f"{chess.square_name(square)}: source {before.symbol() if before else '-'}, target {after.symbol() if after else '-'}")
    if target_features.castling != source_features.castling:
        differences.append(f"castling rights: source {source_features.castling}, target {target_features.castling}")
    if (target_features.en_passant, target_features.legal_en_passant) != (source_features.en_passant, source_features.legal_en_passant):
        differences.append(f"en passant: source {source_features.en_passant or '-'} (legal={source_features.legal_en_passant}), target {target_features.en_passant or '-'} (legal={target_features.legal_en_passant})")
    if (target_features.in_check, target_features.king_attackers) != (source_features.in_check, source_features.king_attackers):
        differences.append(f"king attack/check context: source {source_features.king_attackers}, target {target_features.king_attackers}")
    return Comparison(score, similarities, tuple(differences))
