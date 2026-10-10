"""Named #108 legal-position inventory regressions."""
import chess
import pytest
from app.services.position_inventory import inventory_segment


def test_issue108_uncovered_legal_replies_and_terminal_opponent_positions():
    occurrences, ending_fen = inventory_segment(chess.STARTING_FEN, ['e2e4'], 'white', 0)
    assert len(occurrences) == 2
    terminal = occurrences[-1]
    assert terminal['opponent'] and terminal['authored_reply'] is None
    assert len(terminal['legal_replies']) == 20
    assert 'c7c5' in {reply['move_uci'] for reply in terminal['legal_replies']}
    assert terminal['fen_key'] == ' '.join(chess.Board(ending_fen).fen().split()[:4])


def test_issue108_custom_fen_castling_en_passant_and_illegal_moves():
    castling_board = chess.Board('r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1')
    positions, _ = inventory_segment(castling_board.fen(), [], 'black', 0)
    assert {'e1g1','e1c1'} <= {reply['move_uci'] for reply in positions[0]['legal_replies']}
    no_castling = castling_board.copy()
    no_castling.castling_rights = 0
    other, _ = inventory_segment(no_castling.fen(), [], 'black', 0)
    assert positions[0]['fen_key'] != other[0]['fen_key']
    ep_board = chess.Board('4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1')
    ep, _ = inventory_segment(ep_board.fen(), [], 'black', 0)
    assert 'e5d6' in {reply['move_uci'] for reply in ep[0]['legal_replies']}
    ep_board.ep_square = None
    no_ep, _ = inventory_segment(ep_board.fen(), [], 'black', 0)
    assert ep[0]['fen_key'] != no_ep[0]['fen_key']
    with pytest.raises(ValueError, match='Illegal authored move'):
        inventory_segment(chess.STARTING_FEN, ['e2e5'], 'white', 0)


def test_issue108_segment_checkpoint_preserves_absolute_ply_without_prefix_replay():
    moves = ['g1f3','g8f6','f3g1','f6g8'] * 40
    whole, whole_fen = inventory_segment(chess.STARTING_FEN, moves, 'white', 0)
    board_fen = chess.STARTING_FEN
    joined = []
    for offset in range(0, len(moves), 16):
        chunk, board_fen = inventory_segment(board_fen, moves[offset:offset+16], 'white', offset,
                                             terminal=offset+16 == len(moves))
        joined.extend(chunk)
    assert joined == whole
    assert board_fen == whole_fen


def test_issue108_promotion_pinned_en_passant_and_side_to_move_legality():
    promotion_fen = '4k3/P7/8/8/8/8/8/4K3 w - - 0 1'
    positions, _ = inventory_segment(promotion_fen, [], 'black', 0)
    replies = {reply['move_uci'] for reply in positions[0]['legal_replies']}
    assert {'a7a8q', 'a7a8r', 'a7a8b', 'a7a8n'} <= replies
    assert replies == {move.uci() for move in chess.Board(promotion_fen).legal_moves}
    learner_positions, _ = inventory_segment(promotion_fen, [], 'white', 0)
    assert not learner_positions[0]['opponent'] and learner_positions[0]['legal_replies'] == []
    pinned_fen = 'k3r3/8/8/3pP3/8/8/8/4K3 w - d6 0 1'
    pinned, _ = inventory_segment(pinned_fen, [], 'black', 0)
    assert 'e5d6' not in {reply['move_uci'] for reply in pinned[0]['legal_replies']}
    assert pinned[0]['fen_key'].endswith(' -')
    checked, _ = inventory_segment('r3k2r/8/8/8/8/8/4r3/R3K2R w KQkq - 0 1', [], 'black', 0)
    assert not {'e1g1', 'e1c1'} & {reply['move_uci'] for reply in checked[0]['legal_replies']}
    with pytest.raises(ValueError, match='Invalid inventory'):
        inventory_segment('4k3/8/8/8/8/8/8/4K3 w K - 0 1', [], 'black', 0)


def test_issue108_probability_contract_keeps_unobserved_legal_replies_unknown():
    from app.opening_move_probability_contracts import PositionMoveUniverse, unknown_distribution
    occurrences, _ = inventory_segment(chess.STARTING_FEN, ['e2e4', 'e7e5'], 'white', 0)
    opponent = occurrences[1]
    universe = PositionMoveUniverse(fen_key=opponent['fen_key'],
                                   legal_moves=tuple(reply['move_uci'] for reply in opponent['legal_replies']))
    distribution = unknown_distribution(universe)
    assert len(distribution.moves) == len(opponent['legal_replies']) == 20
    assert distribution.unknown_mass == 1
    assert all(reply.probability is None for reply in distribution.moves)
    assert any(reply.move_uci == 'c7c5' for reply in distribution.moves)
    assert all('probability' not in reply for reply in opponent['legal_replies'])
    terminal, _ = inventory_segment('7k/6Q1/6K1/8/8/8/8/8 b - - 0 1', [], 'white', 0)
    assert terminal[0]['opponent'] and terminal[0]['legal_replies'] == []
