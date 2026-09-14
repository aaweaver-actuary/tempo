from __future__ import annotations

import random

import chess


PIECES = {"Q": chess.QUEEN, "R": chess.ROOK, "B": chess.BISHOP, "N": chess.KNIGHT, "P": chess.PAWN}


def normalized_material(value: str) -> str:
    pieces = value.upper().replace("&", "").replace("+", "").replace(" ", "")
    if pieces.count("K") != 1 or any(piece not in "KQRBNP" for piece in pieces):
        raise ValueError("Each side needs exactly one king and standard piece letters")
    return "K" + "".join(sorted(pieces.replace("K", ""), key="QRBNP".index))


def generate_position(white_material: str, black_material: str, trained_color: str, seed: int | None = None) -> str:
    white = normalized_material(white_material)
    black = normalized_material(black_material)
    if len(white) + len(black) > 7:
        raise ValueError("Tempo currently supports complete seven-piece tablebases")
    rng = random.Random(seed)
    for _ in range(4000):
        board = chess.Board.empty()
        available = list(chess.SQUARES)
        rng.shuffle(available)
        for color, material in ((chess.WHITE, white), (chess.BLACK, black)):
            for symbol in material:
                square = available.pop()
                while symbol == "P" and chess.square_rank(square) in {0, 7}:
                    available.insert(0, square)
                    square = available.pop()
                board.set_piece_at(square, chess.Piece(chess.KING if symbol == "K" else PIECES[symbol], color))
        board.turn = chess.WHITE if trained_color == "white" else chess.BLACK
        board.clear_stack()
        # is_valid() also rejects STATUS_OPPOSITE_CHECK: the non-moving king
        # cannot already be attacked in a historically legal position.
        if board.is_valid() and not board.is_game_over(claim_draw=True):
            return board.fen()
    raise ValueError("Could not generate a legal position for that material")


def category_for_player(category: str) -> str:
    if "win" in category and "loss" not in category:
        return "win"
    if "loss" in category:
        return "loss"
    return "draw"


def move_preserves_target(current: str, next_category_for_opponent: str) -> bool:
    resulting = "loss" if next_category_for_opponent == "win" else "win" if next_category_for_opponent == "loss" else "draw"
    return resulting == current or (current == "draw" and resulting == "win")
