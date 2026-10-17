"""Response-scope classification, independent of database/export persistence."""
from types import SimpleNamespace

import chess
import pytest

from tempo_game_evidence.analysis_export import deviations
from tempo_chess_core import canonical_fen_key
from .factories import observed


@pytest.mark.parametrize('same_policy',[True,False])
def test_C3_existing_authored_position_does_not_become_a_new_content_gap(same_policy):
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
    snapshot=SimpleNamespace(complete=True,serving_current=lambda:True,
        compiled_routes=lambda:[(route,keys,{})],
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
