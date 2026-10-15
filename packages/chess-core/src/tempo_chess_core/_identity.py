"""The legacy Python stable-card algorithm, preserved byte for byte."""
import hashlib


def canonical_fen_key(fen: str) -> str:
    """Keep the first four supplied fields, including castling/en-passant state."""
    return " ".join(fen.split()[:4])


def card_id(starting_fen: str, moves: list[str]) -> str:
    """Hash supplied position/moves without parsing, case folding, or repair."""
    normalized_fen = canonical_fen_key(starting_fen)
    normalized_moves = " ".join(move.strip() for move in moves)
    payload = f"{normalized_fen}\n{normalized_moves}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
