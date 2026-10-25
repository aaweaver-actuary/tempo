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


def test_optional_repertoire_title_absent_preserves_prior_route_bytes():
    import json
    from tempo_learning_runtime.publication_bridge import Route
    raw=projected_bundle()['routes'][0]
    current=Route.model_validate(raw).model_dump(mode='json')
    expected={**raw,'bindings':[{**binding,'reentry':False} for binding in raw['bindings']]}
    assert current==expected
    assert json.dumps(current,sort_keys=True,separators=(',',':'))==json.dumps(expected,sort_keys=True,separators=(',',':'))
    from tempo_learning_runtime.publication_bridge import digest
    # Independently computed from origin/main 1a0fd5c5's complete normalized
    # projected_bundle before introducing the optional repertoire title field.
    assert digest(PublicationBundleV2.model_validate(projected_bundle()).model_dump(mode='json'))==(
        '6f2db1f32eeb4c9fe3f6fa4f5058f5871786fc31de5cbff1b7004444dcef2d93')


@pytest.mark.parametrize('title',['', '   ', 'x'*2001, 7])
def test_explicit_repertoire_title_requires_bounded_nonblank_text(title):
    value=projected_bundle()
    value['routes'][0]['repertoire_title']=title
    with pytest.raises(ValueError):PublicationBundleV2.model_validate(value)


def test_repertoire_titles_are_consistent_while_absent_routes_keep_legacy_meaning():
    from copy import deepcopy
    value=projected_bundle()
    value['routes'][0]['repertoire_title']="Répertoire: King's Indian"
    other=deepcopy(value['routes'][0]);other['route_id']='second-route'
    value['routes'].append(other)
    assert PublicationBundleV2.model_validate(value).routes[0].repertoire_title=="Répertoire: King's Indian"
    other.pop('repertoire_title')
    assert PublicationBundleV2.model_validate(value).routes[1].repertoire_title is None
    other['repertoire_title']='Conflicting title'
    with pytest.raises(ValueError,match='Conflicting titles'):PublicationBundleV2.model_validate(value)


def test_titled_unbound_foreign_route_cannot_escape_membership_ownership_locks():
    from copy import deepcopy
    value=projected_bundle()
    foreign=deepcopy(value['routes'][0])
    foreign.update(repertoire_id='foreign',route_id='foreign-route',bindings=[])
    value['routes'].append(foreign)
    # Preserve existing untitled extra-route behavior; it carries no name write.
    assert PublicationBundleV2.model_validate(value).routes[1].repertoire_title is None
    foreign['repertoire_title']='Unauthorized foreign rename'
    with pytest.raises(ValueError,match='accepted opening membership'):
        PublicationBundleV2.model_validate(value)
