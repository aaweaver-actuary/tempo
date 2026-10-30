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
        position_key = canonical_fen_key(fen)
        # FEN differs only in the EP field between these policies. Preserve the
        # raw field for provenance, dropping it for comparison unless legal.
        comparison_key = position_key
        if board.ep_square is not None and not board.has_legal_en_passant():
            comparison_key = position_key.rsplit(" ", 1)[0] + " -"
        positions.append({"ply": ply, "fen": fen, "position_key": position_key,
                          "comparison_key": comparison_key,
                          "turn": "white" if board.turn else "black", "move": move})
        if move is not None:
            parsed = chess.Move.from_uci(move)
            if parsed not in board.legal_moves:
                raise ValueError("illegal_index_source")
            board.push(parsed)
    return positions
