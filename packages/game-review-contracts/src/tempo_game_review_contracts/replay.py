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


class ReplaySession:
    """Share exact route prefixes within one bounded caller operation.

    Cached nodes retain immutable FEN facts; returned frames are fresh dicts.
    A session has no storage, authority, or lifetime beyond its caller.
    """

    _max_nodes = 16384

    def __init__(self):
        self._roots = {}
        self._node_count = 0

    @staticmethod
    def _node(board):
        fen = board.fen(en_passant="fen")
        position_key = canonical_fen_key(fen)
        comparison_key = position_key
        if board.ep_square is not None and not board.has_legal_en_passant():
            comparison_key = position_key.rsplit(" ", 1)[0] + " -"
        return (fen, position_key, comparison_key,
                "white" if board.turn else "black", {})

    def replay_line(self, starting_fen, moves):
        if not isinstance(starting_fen, str) or any(not isinstance(move, str) for move in moves):
            return replay_line(starting_fen, moves)
        node = self._roots.get(starting_fen)
        if node is None:
            if self._node_count >= self._max_nodes:
                return replay_line(starting_fen, moves)
            board = chess.Board(starting_fen)
            if not board.is_valid():
                raise ValueError("invalid_starting_position")
            node = self._node(board)
            self._roots[starting_fen] = node
            self._node_count += 1
        board = None
        positions = []
        for ply in range(len(moves) + 1):
            fen, position_key, comparison_key, turn, children = node
            move = moves[ply] if ply < len(moves) else None
            positions.append({"ply": ply, "fen": fen, "position_key": position_key,
                              "comparison_key": comparison_key, "turn": turn, "move": move})
            if move is not None:
                child = children.get(move)
                if child is None:
                    if self._node_count >= self._max_nodes:
                        return replay_line(starting_fen, moves)
                    if board is None:
                        board = chess.Board(fen)
                    parsed = chess.Move.from_uci(move)
                    if parsed not in board.legal_moves:
                        raise ValueError("illegal_index_source")
                    board.push(parsed)
                    child = self._node(board)
                    children[move] = child
                    self._node_count += 1
                else:
                    board = None
                node = child
        return positions
