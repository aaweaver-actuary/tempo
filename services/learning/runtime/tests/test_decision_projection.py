"""Learning validates learner decisions inside the unchanged executable."""
import chess
import pytest
from tempo_learning_runtime.publication_bridge import Binding,PublicationBundleV2


def projected_bundle():
    definition={'card_id':'content-card','decision_id':'primary','policy_id':'policy','source_revision':1,
        'publication_id':'publication','publication_generation':1,'exercise_revision':1,'starting_fen':chess.STARTING_FEN,'prompt':'Play the opening',
        'specification':{'type':'move_line','mode':'stepwise_line','grading_policy':'reference','accepted_lines':[['e2e4','e7e5','g1f3','b8c6']]}}
    return {'schema_version':2,'source_digest':'a'*64,
        'publication':{'contract_version':1,'workspace_id':'test','publication_id':'publication','source_revision':1,'publication_generation':1,'built_at':'2026-10-17T00:00:00Z','definitions':[definition]},
        'facts':[{'card_id':'content-card','content_type':'opening','trained_color':'black','memberships':['rep'],'due_date':'2026-10-17','route_order':0}],
        'routes':[{'repertoire_id':'rep','route_id':'route','policy_id':'policy','learner_color':'black','starting_fen':chess.STARTING_FEN,'moves':['e2e4','e7e5','g1f3','b8c6'],
            'bindings':[{'ply':1,'card_id':'content-card','exercise_ply':1,'decision_id':'primary'},
                        {'ply':3,'card_id':'content-card','exercise_ply':3,'decision_id':'interior'}]}],
        'legacy_aliases':{'content-card':'original-physical-card'}}


def test_C3_projected_decisions_retain_original_whole_executable_and_alias():
    bundle=PublicationBundleV2.model_validate(projected_bundle())
    assert bundle.publication.definitions[0].starting_fen==chess.STARTING_FEN
    assert bundle.publication.definitions[0].specification.accepted_lines==[['e2e4','e7e5','g1f3','b8c6']]
    assert bundle.legacy_aliases=={'content-card':'original-physical-card'}
    assert bundle.routes[0].bindings[1].decision_id=='interior'


@pytest.mark.parametrize('fault',['offset','position','identity'])
def test_C3_projection_cannot_relabel_another_position_or_decision(fault):
    value=projected_bundle();binding=value['routes'][0]['bindings'][1]
    if fault=='offset':binding['exercise_ply']=4
    elif fault=='position':binding['exercise_ply']=1
    else:binding['decision_id']='primary'
    with pytest.raises(ValueError):PublicationBundleV2.model_validate(value)


def test_C3_projection_metadata_absent_keeps_prior_route_digest_bytes():
    old=Binding(ply=0,card_id='card').model_dump(mode='json')
    assert old=={'ply':0,'card_id':'card','reentry':False}
    assert Binding(ply=1,card_id='card',exercise_ply=1,decision_id='primary').model_dump(mode='json')['exercise_ply']==1
