"""AS-01,03–07,19,22: deterministic structural advice with legal chess fixtures."""
import json

import chess
import pytest

from app.services.opening_segmentation import (decision_identity, opening_position_key,
    presentation_occurrences, recommendation_estimate)
from app.services.cards import card_id


def presentation(moves, start=chess.STARTING_FEN, color='white'):
    return {'id': card_id(start, moves), 'start_fen': start, 'moves_json': json.dumps(moves),
            'revision': 1, 'trained_color': color}


def test_shared_trunk_preserves_coverage_with_20_tests_instead_of_48():
    # Count-only fixture intentionally synthetic; legal fixtures below prove traversal.
    occurrences = tuple({
        'card_id': str(index), 'decision_index': 3, 'decision_count': 6,
        'next_context': (f'branch-{index}',), 'trained_color': 'white',
        'start_fen': chess.STARTING_FEN, 'cut_fen': chess.STARTING_FEN,
        'ending_fen': chess.STARTING_FEN, 'prefix_moves': ('common-1','common-2','common-3','common-4'),
        'branch_moves': (f'branch-{index}-1', f'branch-{index}-2'),
    } for index in range(8))
    result = recommendation_estimate('shared_trunk', occurrences)
    assert result is not None
    assert (result['decisions_before'], result['decisions_after'], result['decisions_avoided']) == (48, 20, 28)
    assert len(result['segments']) == 9
    assert sum(part['tested_decisions'] for part in result['segments']) == 20


def test_legal_early_branch_retains_opponent_cue_and_ends_on_learner_move():
    routes = [presentation(['e2e4', reply, 'g1f3']) for reply in ['e7e5', 'c7c5', 'e7e6']]
    occurrences = tuple(presentation_occurrences('rep', route)[0] for route in routes)
    result = recommendation_estimate('shared_trunk', occurrences)
    assert result['decisions_before'] == 6 and result['decisions_after'] == 4
    branches = [part for part in result['segments'] if part['role'] == 'branch']
    assert {part['moves'][0] for part in branches} == {'e7e5', 'c7c5', 'e7e6'}
    assert all(chess.Board(part['ending_fen']).turn == chess.BLACK for part in branches)


def test_duplicate_routes_and_existing_shared_cards_do_not_inflate_support():
    occurrence = presentation_occurrences('rep', presentation(['e2e4', 'e7e5', 'g1f3']))[0]
    assert recommendation_estimate('shared_trunk', (occurrence, occurrence)) is None


def test_compatible_transposition_shares_suffix_but_preserves_incoming_bridges():
    routes = [['g1f3','g8f6','g2g3','g7g6','f1g2'], ['g2g3','g7g6','g1f3','g8f6','f1g2']]
    occurrences = tuple(presentation_occurrences('rep', presentation(moves))[-1] for moves in routes)
    assert occurrences[0]['decision_id'] == occurrences[1]['decision_id']
    assert occurrences[0]['incoming_key'] != occurrences[1]['incoming_key']
    result = recommendation_estimate('transposition', occurrences)
    assert result['decisions_before'] == 6 and result['decisions_after'] == 5
    assert sorted(part['role'] for part in result['segments']) == ['bridge','bridge','shared_continuation']


def test_decision_identity_preserves_policy_color_turn_castling_and_legal_en_passant():
    base = chess.STARTING_FEN
    identifier = decision_identity('rep', base, 'white', 'e2e4')
    for fen in [base.replace(' w ', ' b '), base.replace('KQkq', 'KQ')]:
        assert decision_identity('rep', fen, 'white', 'e2e4') != identifier
    assert decision_identity('other', base, 'white', 'e2e4') != identifier
    assert decision_identity('rep', base, 'white', 'd2d4') != identifier
    assert decision_identity('rep', base, 'white', 'e2e4', 2) != identifier
    assert decision_identity('rep', base, 'black', 'e2e4') != identifier
    assert decision_identity('rep', base.replace('0 1', '17 80'), 'white', 'e2e4') == identifier
    ep = '4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2'
    assert opening_position_key(ep) != opening_position_key(ep.replace('d6', '-'))
    # A syntactic but legally unusable EP square is not opening knowledge.
    assert opening_position_key(base.replace(' - ', ' e3 ')) == opening_position_key(base)


def test_black_custom_root_and_opponent_start_count_actual_learner_decisions():
    black = presentation_occurrences('rep', presentation(['e2e4','e7e5','g1f3','b8c6'], color='black'))
    assert len(black) == 2 and black[0]['move_offset'] == 1
    board = chess.Board(); board.push_uci('e2e4')
    interior = presentation_occurrences('rep', presentation(['e7e5','g1f3','b8c6','f1c4'], start=board.fen()))
    assert len(interior) == 2 and interior[0]['prefix_moves'] == ('e7e5','g1f3')
    assert interior[0]['start_fen'] == board.fen()


def test_cycle_preserves_bounded_occurrences_without_walk_enumeration():
    moves = ['g1f3','g8f6','f3g1','f6g8','g1f3']
    occurrences = presentation_occurrences('rep', presentation(moves))
    assert len(occurrences) == 3
    assert occurrences[0]['decision_id'] == occurrences[-1]['decision_id']
    assert occurrences[0]['incoming_key'] != occurrences[-1]['incoming_key']


def test_illegal_or_unbounded_presentations_are_actionable_errors():
    with pytest.raises(ValueError, match='Illegal'):
        presentation_occurrences('rep', presentation(['e2e5']))
    with pytest.raises(ValueError, match='bounded'):
        presentation_occurrences('rep', presentation(['g1f3'] * 41))


def test_no_savings_or_insufficient_support_does_not_claim_improvement():
    occurrence = presentation_occurrences('rep', presentation(['e2e4']))[0]
    assert recommendation_estimate('transposition', (occurrence,)) is None
