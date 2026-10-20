"""Pure legal replay extracted from Game Evidence source.replay_positions."""
import chess
from tempo_chess_core import canonical_fen_key


def replay_line(starting_fen, moves):
    board = chess.Board(starting_fen)
    if not board.is_valid():
        raise ValueError("invalid_starting_position")
    positions = []
    for ply in range(len(moves) + 1):
        fen = board.fen(en_passant="fen")
        move = moves[ply] if ply < len(moves) else None
        positions.append({"ply": ply, "fen": fen, "position_key": canonical_fen_key(fen),
                          "comparison_key": canonical_fen_key(board.fen(en_passant="legal")),
                          "turn": "white" if board.turn else "black", "move": move})
        if move is not None:
            parsed = chess.Move.from_uci(move)
            if parsed not in board.legal_moves:
                raise ValueError("illegal_index_source")
            board.push(parsed)
    return positions
