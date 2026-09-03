import hashlib


def card_id(starting_fen: str, moves: list[str]) -> str:
    """Build the stable deduplication key specified by the product."""
    # Position identity keeps placement, turn, castling, and en-passant state.
    # Halfmove/fullmove clocks do not change which opening position is tested.
    normalized_fen = " ".join(starting_fen.split()[:4])
    normalized_moves = " ".join(move.strip() for move in moves)
    payload = f"{normalized_fen}\n{normalized_moves}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
