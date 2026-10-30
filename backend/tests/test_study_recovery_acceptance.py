"""Owning acceptance-harness checks; no database or Docker allocation."""
import pytest
import chess

from test_product_game_lapse_postgres import postgres_fixture_profile
from fixtures.study_recovery_content import Shape, publication


def test_recovery_profile_keeps_default_native_allocation_and_is_explicit():
    assert postgres_fixture_profile('default') == dict(memory_mib=384, storage='tmpfs')
    assert postgres_fixture_profile('study-recovery') == dict(memory_mib=1024, storage='owned-volume')
    for unsupported in (None, '', 'live', '1024', 'unlimited'):
        with pytest.raises(ValueError, match='unknown_owned_postgres_fixture_profile'):
            postgres_fixture_profile(unsupported)


def test_recovery_publication_is_legal_complete_short_card_scope_with_real_standard_target():
    shape = Shape('white', 12, 216, 32, 48, 84, 216, 7)
    generated = publication(shape, 'rep', 'fixture')
    assert generated['generated']['routes'] == shape.routes
    assert generated['generated']['authored_plies'] == shape.authored_plies
    assert generated['generated']['graphs'] == shape.graphs
    assert generated['target']['starting_fen'] == chess.STARTING_FEN
    assert generated['target']['occurrence_ply'] == 9
    own = {}
    import json
    for row in generated['lines']:
        board = chess.Board(row['start_fen'])
        for uci in json.loads(row['moves_json']):
            move = chess.Move.from_uci(uci)
            assert move in board.legal_moves
            if board.turn:
                key = ' '.join(board.fen(en_passant='legal').split()[:4])
                assert own.setdefault(key, uci) == uci
            board.push(move)
    cards = {row['id']: row for row in generated['cards']}
    for graph in generated['graphs']:
        assert graph['moves_json'] == cards[graph['card_id']]['moves_json']
        board = chess.Board(graph['starting_fen'])
        for uci in json.loads(graph['moves_json']):
            board.push_uci(uci)
