"""Response-scope classification, independent of database/export persistence."""
from types import SimpleNamespace

import chess
import pytest

from tempo_game_evidence.analysis_export import deviations
from tempo_chess_core import canonical_fen_key
from .factories import observed


@pytest.mark.parametrize('same_policy',[True,False])
@pytest.mark.parametrize('projected',[False,True])
def test_C3_existing_authored_position_does_not_become_a_new_content_gap(same_policy,projected):
    game,positions=observed(['e2e4','c7c5','g1f3'])
    route=SimpleNamespace(repertoire_id='rep',policy_id='policy',route_id='authored',learner_color='white',moves=['e2e4','e7e5','g1f3'])
    board=chess.Board();keys=[canonical_fen_key(board.fen())]
    for move in route.moves:
        board.push_uci(move);keys.append(canonical_fen_key(board.fen()))
    scope={'repertoire_id':'rep','policy_id':'policy','complete':True,'digest':'scope',
        'route_contexts':[{'route_id':'authored','ply':1}],
        'replies':[{'move_uci':'c7c5','status':'uncovered','responses':[]}]}
    # This executable belongs to another authored route. Position equality alone
    # is neither absence of content nor permission to re-enter that route.
    existing=SimpleNamespace(card_id='existing-content',decision_id='decision',policy_id='policy' if same_policy else 'other-policy',starting_fen=positions[2]['fen'])
    compiled=[(route,keys,{})]
    if projected:
        from tempo_game_evidence.snapshot import Binding
        existing.starting_fen=positions[1]['fen']
        blueprint={key:getattr(existing,key) for key in ('card_id','decision_id','policy_id','starting_fen')}
        blueprint['specification']={'type':'move_line','mode':'stepwise_line','grading_policy':'reference','accepted_lines':[['c7c5','g1f3']]}
        existing.model_dump=lambda **kwargs:blueprint
        other=SimpleNamespace(repertoire_id='rep',policy_id=existing.policy_id,route_id='other',learner_color='white',moves=['c7c5','g1f3'])
        other_keys=[position['comparison_key'] for position in positions[1:]]
        compiled.append((other,other_keys,{1:Binding(ply=1,card_id=existing.card_id,exercise_ply=1,decision_id=existing.decision_id)}))
    snapshot=SimpleNamespace(complete=True,serving_current=lambda:True,
        compiled_routes=lambda:compiled,
        publication=SimpleNamespace(source_revision=1,publication_generation=1,definitions=[existing]),
        content_export={'export_digest':'export','legal_replies':[{'position_key':positions[1]['comparison_key'],'scopes':[scope]}]})
    finding=deviations(game,positions,snapshot)[0]
    if same_policy:
        assert finding['status']=='unknown'
        assert finding['reason']=='existing_authored_position_reentry_unverified'
        assert finding['response_inventory']['complete'] is False
        assert finding['response_inventory']['existing_position_content_ids']==['existing-content']
    else:
        assert finding['status']=='established_opponent_deviation'
        assert finding['response_inventory']['complete'] is True
