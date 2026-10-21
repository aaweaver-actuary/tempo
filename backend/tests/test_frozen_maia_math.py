import json
import chess
import pytest
from app.study.openings.frozen_math import anchor, represented_reach, card_decisions, greedy_order
from app.chess.opening_segments import decision_identity


def scenario(prefix='', color='white', **extra):
    return {'name':'fixture','repertoire_id':'policy','color':color,'prefix':prefix.split(),'weight':.1,**extra}


def line(san, identifier='line'):
    board=chess.Board();moves=[]
    for move in san.split():
        parsed=board.parse_san(move);moves.append(parsed.uci());board.push(parsed)
    return {'id':identifier,'repertoire_id':'policy','start_fen':chess.STARTING_FEN,'moves_json':json.dumps(moves),'trained_color':'white'}


def distribution(fen, assigned=None):
    legal=[move.uci() for move in chess.Board(fen).legal_moves]
    assigned=assigned or {}
    remainder=[move for move in legal if move not in assigned]
    rest=(1-sum(assigned.values()))/len(remainder) if remainder else 0
    return [{'move_uci':move,'probability':assigned.get(move,rest)} for move in legal]


def test_all_ten_exact_conditioning_anchors():
    from pathlib import Path
    config=json.loads((Path(__file__).parents[2]/'scripts/frozen-maia-order.json').read_text())
    assert len(config['scenarios']) == 10
    expected_plies=[5,5,2,0,4,5,3,1,3,1]
    assert [anchor(row).ply() for row in config['scenarios']] == expected_plies
    assert all(row['weight']==.1 for row in config['scenarios'])


def test_prescribed_learner_move_is_not_maia_downweighted_and_anchor_factors_excluded():
    inputs=[]
    def predict(fen):
        board=chess.Board(fen);inputs.append(board.ply())
        return distribution(fen,{'e7e5':.6} if board.ply()==1 else {'b8c6':.5})
    result=represented_reach(scenario('e4'),[line('e4 e5 Nf3 Nc6 Bb5')],predict)
    board=chess.Board();board.push_san('e4');board.push_san('e5');board.push_san('Nf3');board.push_san('Nc6')
    assert result['reach'][decision_identity('policy',board.fen(),'white','f1b5')] == pytest.approx(.3)
    assert inputs == [1,3]
    assert result['reach'][decision_identity('policy',chess.STARTING_FEN,'white','e2e4')]==1


def test_kid_first_move_conditioning_retains_later_e4_and_d4_c4():
    policy=scenario(color='black',exclude_first='e2e4')
    result=represented_reach(policy,[line('d4 Nf6 c4 g6 e4 Bg7')],lambda fen:distribution(fen,{'e2e4':.4,'d2d4':.3} if chess.Board(fen).ply()==0 else None))
    assert result['reach'][decision_identity('policy',anchor(scenario('d4')).fen(),'black','g8f6')]==pytest.approx(.5)
    assert len(result['reach'])==3


def test_uniform_distinct_learner_choices_ignore_duplicate_imports_and_outside_mass():
    paths=[line('e4 e5 Nf3'),line('e4 e5 Nc3','alternative'),line('e4 e5 Nf3','duplicate')]
    result=represented_reach(scenario('e4'),paths,lambda fen:distribution(fen,{'e7e5':.6}))
    board=anchor(scenario('e4 e5'))
    assert result['reach'][decision_identity('policy',board.fen(),'white','g1f3')]==pytest.approx(.3)
    assert result['reach'][decision_identity('policy',board.fen(),'white','b1c3')]==pytest.approx(.3)
    assert len(result['alternative_assumptions'])==1


def test_repeated_decisions_and_duplicate_routes_do_not_inflate_first_hit():
    paths=[line('Nf3 Nf6 Ng1 Ng8 Nf3','cycle'),line('Nf3 Nf6 Ng1 Ng8 Nf3','duplicate')]
    result=represented_reach(scenario(),paths,distribution)
    assert result['reach'][decision_identity('policy',chess.STARTING_FEN,'white','g1f3')]==1


def test_ruy_italian_policy_identity_is_independent():
    paths=[line('e4 e5 Nf3 Nc6 Bb5 a6 Ba4')]
    ruy=represented_reach(scenario('e4 e5 Nf3 Nc6 Bb5'),paths,distribution)
    italian=represented_reach({**scenario('e4 e5 Nf3 Nc6 Bc4'),'repertoire_id':'italian'},[line('e4 e5 Nf3 Nc6 Bc4 Bc5 d3')],distribution)
    assert not ruy['reach'].keys() & italian['reach'].keys()
    assert represented_reach(scenario('e4 e5 Nf3 Nc6 Bb5'),paths,distribution)['reach']==ruy['reach']


def test_marginal_multidecision_overlap_and_unknown_are_distinct():
    cards=[{'id':'a','decisions':['trunk','branch'],'baseline_order':0}, {'id':'b','decisions':['trunk'],'baseline_order':1}]
    order=greedy_order(cards,{'trunk':.8,'branch':.3})
    assert [(row['id'],row['marginal_score']) for row in order]==[('a',1.1),('b',0)]
    with pytest.raises(ValueError,match='UNKNOWN'):greedy_order(cards,{'trunk':.8})


def test_missing_inference_is_unknown_not_zero():
    with pytest.raises(ValueError,match='Incomplete'):
        represented_reach(scenario('e4'),[line('e4 e5 Nf3')],lambda fen:[])


def test_mutually_exclusive_transpositions_sum_once_for_shared_decision():
    paths=[line('Nf3 d5 g3 Nf6 Bg2'),line('g3 d5 Nf3 Nf6 Bg2','transpose')]
    def predict(fen):
        board=chess.Board(fen)
        return distribution(fen,{'d7d5':.6} if board.ply()==1 else {'g8f6':.5})
    result=represented_reach(scenario(),paths,predict)
    board=anchor(scenario('Nf3 d5 g3 Nf6'))
    assert result['reach'][decision_identity('policy',board.fen(),'white','f1g2')]==pytest.approx(.3)


def test_anchor_fen_accepts_transposed_arrivals_without_counting_arrival_mass():
    paths=[line('Nf3 d5 g3 Nf6 Bg2'),line('g3 d5 Nf3 Nf6 Bg2','transpose')]
    predicted=[]
    def predict(fen):
        predicted.append(fen)
        return distribution(fen,{'g8f6':.5})
    policy=scenario('Nf3 d5 g3')
    result=represented_reach(policy,paths,predict)
    board=anchor(scenario('Nf3 d5 g3 Nf6'))
    decision=decision_identity('policy',board.fen(),'white','f1g2')
    assert result['reach'][decision]==pytest.approx(.5)
    transposed_only=represented_reach(policy,paths[1:],predict)
    assert transposed_only['reach'][decision]==pytest.approx(.5)
    assert [route['moves'] for route in result['routes']]==[['g8f6','f1g2']]*2
    assert all(chess.Board(fen).ply()==3 for fen in predicted)
    from app.chess.opening_segments import opening_position_key
    assert result['position_reach'][opening_position_key(anchor(policy).fen())]==1
    assert result['position_reach'][opening_position_key(board.fen())]==pytest.approx(.5)


def test_anchor_position_identity_preserves_turn_castling_and_legal_en_passant():
    from app.chess.opening_segments import opening_position_key
    anchor_board=anchor(scenario('e4 a6 e5 d5'))
    canonical=opening_position_key(anchor_board.fen())
    assert opening_position_key(anchor_board.fen().rsplit(' ',2)[0]+' 9 17')==canonical
    for replacement in ('turn','castling','en_passant'):
        changed=anchor_board.copy()
        if replacement=='turn':changed.turn=not changed.turn
        elif replacement=='castling':changed.castling_rights=0
        else:changed.ep_square=None
        assert opening_position_key(changed.fen())!=canonical


def test_position_probability_is_not_sum_of_repeated_answer_decisions():
    from app.chess.opening_segments import opening_position_key
    result=represented_reach(scenario(),[line('Nf3 Nf6 Ng1 Ng8 e4')],distribution)
    assert result['position_reach'][opening_position_key(chess.STARTING_FEN)]==1
    assert len([key for key,value in result['reach'].items() if value>0])==3


def test_transposed_arrivals_share_distinct_learner_policy_weights():
    paths=[line('Nf3 d5 g3 Nf6 Bg2'),line('g3 d5 Nf3 Nf6 d3','transpose')]
    def predict(fen):
        return distribution(fen,{'d7d5':.6} if chess.Board(fen).ply()==1 else {'g8f6':.5})
    result=represented_reach(scenario(),paths,predict)
    board=anchor(scenario('Nf3 d5 g3 Nf6'))
    for move in ('f1g2','d2d3'):
        assert result['reach'][decision_identity('policy',board.fen(),'white',move)]==pytest.approx(.075)
    # Each arrival has probability .5*.6*.5=.15; each answer has the same
    # .5 policy weight, even when one route does not represent its continuation.
    alternatives=[row for row in result['alternative_assumptions'] if row['moves']==['d2d3','f1g2']]
    assert len(alternatives)==1 and alternatives[0]['weight']==.5


def test_explicit_learner_choice_weights_and_invalid_kid_denominator():
    from app.chess.opening_segments import opening_position_key
    policy=scenario(choice_weights={opening_position_key(chess.STARTING_FEN):{'e2e4':.8,'d2d4':.2}})
    result=represented_reach(policy,[line('e4'),line('d4','d4')],distribution)
    assert result['reach'][decision_identity('policy',chess.STARTING_FEN,'white','e2e4')]==.8
    with pytest.raises(ValueError,match='denominator'):
        represented_reach(scenario(color='black',exclude_first='e2e4'),[line('d4 Nf6')],lambda fen:distribution(fen,{'e2e4':1}))
