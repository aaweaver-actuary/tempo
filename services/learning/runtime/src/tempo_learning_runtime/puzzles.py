import chess


def validate_puzzle_record(record: dict[str, object]) -> tuple[str, list[str]]:
    """Validate the canonical Lichess setup move and complete solution."""
    source_fen = str(record["FEN"])
    raw_moves = record["Moves"]
    moves = raw_moves.split() if isinstance(raw_moves, str) else list(raw_moves)
    if len(moves) < 2:
        raise ValueError("Puzzle needs a setup move and solution")
    board = chess.Board(source_fen)
    for value in moves:
        move = chess.Move.from_uci(str(value))
        if move not in board.legal_moves:
            raise ValueError(f"Illegal puzzle move {value}")
        board.push(move)
        if value == moves[0]:
            training_fen = board.fen()
    return training_fen, [str(move) for move in moves[1:]]
