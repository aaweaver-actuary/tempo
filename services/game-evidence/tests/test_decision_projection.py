"""A verified decision is distinct from the root of its physical card."""
import copy
import chess
import pytest
from tempo_game_evidence.snapshot import ComparisonSnapshot
from tempo_game_evidence.comparison import compare_game
from .factories import snapshot,observed


def projected_snapshot():
    moves=['e2e4','e7e5','g1f3','b8c6']
    value=snapshot(moves=moves,color='black',binding_ply=1)
    definition=value['publication']['definitions'][0]
    definition['starting_fen']=chess.STARTING_FEN
    definition['specification'].update(accepted_lines=[moves],mode='stepwise_line')
    value['routes'][0]['bindings']=[{'ply':1,'card_id':definition['card_id'],'exercise_ply':1,'decision_id':definition['decision_id']},
        {'ply':3,'card_id':definition['card_id'],'exercise_ply':3,'decision_id':'interior'}]
    return value


def test_C3_miss_uses_projected_decision_and_preserves_full_card():
    accepted=ComparisonSnapshot.model_validate(projected_snapshot())
    game,positions=observed(['e2e4','e7e5','g1f3','d7d6'],color='black')
    findings=compare_game(game,positions,accepted)
    assert [finding['status'] for finding in findings]==['matched','missed']
    assert findings[1]['decision_id']=='interior' and findings[1]['expected_moves']==['b8c6']
    assert findings[1]['card_id']==accepted.publication.definitions[0].card_id
    assert findings[1]['decision_position_key']==' '.join(positions[3]['fen'].split()[:4])
    assert accepted.publication.definitions[0].starting_fen==chess.STARTING_FEN


@pytest.mark.parametrize('fault',['identity','offset'])
def test_C3_projection_requires_correct_position_and_unambiguous_identity(fault):
    value=projected_snapshot()
    second=value['routes'][0]['bindings'][1]
    if fault=='identity':second['decision_id']=value['publication']['definitions'][0]['decision_id']
    else:second['exercise_ply']=1
    with pytest.raises(ValueError):ComparisonSnapshot.model_validate(value)
