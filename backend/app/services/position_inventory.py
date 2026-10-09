"""Source-independent legal replies and bounded authored-route traversal.

No probability is inferred here. Canonical identity intentionally follows Tempo's
legal en-passant FEN convention rather than raw, non-capturable FEN targets.
"""
from __future__ import annotations

import chess
from .repertoire_comparison import canonical_fen

INVENTORY_VERSION = 1
SEGMENT_PLIES = 16


def inventory_segment(start_fen: str, moves: list[str], trained_color: str,
                      first_ply: int, *, terminal: bool = True) -> tuple[list[dict], str]:
    """Traverse one slice; emit its ending position only on the final slice."""
    if trained_color not in ('white', 'black'):
        raise ValueError('Unknown trained color')
    board = chess.Board(start_fen)
    if not board.is_valid():
        raise ValueError('Invalid inventory starting position')
    learner_turn = chess.WHITE if trained_color == 'white' else chess.BLACK
    occurrences = []
    for local_ply in range(len(moves) + int(terminal)):
        authored_move = moves[local_ply] if local_ply < len(moves) else None
        if authored_move is not None:
            try:
                parsed_move = chess.Move.from_uci(authored_move)
            except ValueError as error:
                raise ValueError(f'Illegal authored move at ply {first_ply+local_ply}: {authored_move}') from error
            if parsed_move not in board.legal_moves:
                raise ValueError(f'Illegal authored move at ply {first_ply+local_ply}: {authored_move}')
        opponent = board.turn != learner_turn
        legal_replies = []
        if opponent:
            for legal_move in sorted(board.legal_moves, key=lambda move: move.uci()):
                reply_board = board.copy(stack=False)
                reply_board.push(legal_move)
                legal_replies.append({'move_uci': legal_move.uci(),
                                      'resulting_fen_key': canonical_fen(reply_board.fen())})
        occurrences.append({'ply': first_ply+local_ply, 'fen': board.fen(),
                            'fen_key': canonical_fen(board.fen()), 'opponent': opponent,
                            'authored_reply': authored_move if opponent else None,
                            'authored_move': authored_move, 'legal_replies': legal_replies})
        if authored_move is not None:
            board.push(parsed_move)
    return occurrences, board.fen()
