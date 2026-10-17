"""Synthetic serialized A-shape negatives; actual producer acceptance is separate."""
import json
from pathlib import Path
from copy import deepcopy
import chess
import pytest
from tempo_analysis_pilot.inputs import normalize,fingerprint


def export_fixture(directory):
    board=chess.Board();moves=['e2e4','a7a6','g1f3'];positions=[]
    for ply in range(len(moves)+1):
        positions.append({'ply':ply,'fen':board.fen(en_passant='fen'),'move':moves[ply] if ply<len(moves) else None,'game_id':'game','game_revision':1})
        if ply<len(moves):board.push_uci(moves[ply])
    opponent=chess.Board(positions[1]['fen'])
    route={'repertoire_id':'rep','policy_id':'policy','route_id':'route','repertoire_key':'white','policy_key':'main','route_key':'e4-e5','learner_color':'white','starting_fen':chess.STARTING_FEN,'moves':['e2e4','e7e5','g1f3'],'bindings':[{'ply':0,'card_id':'root','reentry':False},{'ply':2,'card_id':'nf3','reentry':False}]}
    game={'game_id':'game','revision':1,'source_digest':'synthetic','observed_color':'white','starting_fen':chess.STARTING_FEN,'moves':moves,'played_at':None}
    records={'games':[game],'positions':positions,'routes':[route],'legal_replies':[{'position_key':' '.join(opponent.fen().split()[:4]),'complete':True,'generation':1,'moves':[m.uci() for m in opponent.legal_moves]}],
       'definitions':[{'card_id':'root','specification':{'type':'move_line'}},{'card_id':'nf3','specification':{'type':'move_line'}}],
       'comparisons':[{'game':game,'findings':[{'ply':0,'policy_id':'policy','status':'matched','explanation':'accepted_repertoire_move','card_id':'root','decision_id':'decision','expected_moves':['e2e4'],'memberships':[{'repertoire_id':'rep','route_id':'route'}],'encounter_id':'encounter'}],'delivery':{'status':'not_required'}}],
       'deviations':[{'game_id':'game','game_revision':1,'ply':1,'opponent_move':'a7a6','decision_ply':2,'decision_fen':positions[2]['fen'],'history':moves[:2],'route_contexts':[{'repertoire_id':'rep','policy_id':'policy','route_id':'route','route_ply':1}],'status':'established_opponent_deviation','reason':'complete_route_scope','response_inventory':{'complete':True,'source_revision':1,'accepted_responses':[]},'reentry_evidence':[]}]}
    inventory=records['legal_replies'][0]
    inventory['scopes']=[{'starting_fen':chess.STARTING_FEN,'history':['e2e4'],'learner_color':'white','repertoire_id':'rep','policy_id':'policy','complete':True,'source_revision':1,'digest':'4'*64,'replies':[{'move_uci':move,'status':'covered' if move=='e7e5' else 'uncovered'} for move in inventory['moves']]}]
    manifest={'schema_version':1,'kind':'tempo-analysis-inputs','input_id':'synthetic-A-shape','revision':1,'workspace_id':'fixture','source':{'source_id':'source','source_key':'source','source_digest':'0'*64,'source_revision':1,'inventory_version':1},'publication':{'publication_id':'publication','publication_generation':1,'source_revision':1},'activation':{'status':'activated','workspace_id':'fixture','publication_id':'publication','publication_generation':1,'activation_id':'activation','activation_revision':1},'current_serving':{'status':'active','workspace_id':'fixture','publication_id':'publication','publication_generation':1,'activation_revision':1},'publication_digest':'1'*64,'content_export_digest':'2'*64,'snapshot':{'snapshot_id':'snapshot','revision':1,'digest':'3'*64},'activation_origin':{'transport':'authenticated_learning_http','endpoint':'http://127.0.0.1:8000'},'complete':True,'readiness':'ready','provenance':{'kind':'synthetic'},'counts':{kind:len(items) for kind,items in records.items()},'pages':[]}
    for kind,items in records.items():
        page={'schema_version':1,'input_id':manifest['input_id'],'workspace_id':'fixture','revision':1,'kind':kind,'page_index':0,'items':items};filename=kind+'-000000.json'
        (directory/filename).write_text(json.dumps(page));manifest['pages'].append({'kind':kind,'page_index':0,'filename':filename,'count':len(items),'digest':fingerprint(page)})
    manifest['manifest_digest']=fingerprint(manifest);(directory/'manifest.json').write_text(json.dumps(manifest));return manifest


def test_serialized_A_shape_maps_original_comparison_and_deviation(tmp_path):
    export_fixture(tmp_path);manifest,pages=normalize(tmp_path,workspace='fixture',selected_game_ids=['game'])
    assert manifest['producer_manifest']['provenance']['kind']=='synthetic'
    assert pages[0]['games'][0]['contexts'][-1]['coverage']=='uncovered'
    assert manifest['coverage_scopes'][0]['covered_replies']==['e7e5']


def test_no_implicit_all_account_or_partial_selection(tmp_path):
    export_fixture(tmp_path)
    for ids in ([],['other'],['game','game']):
        with pytest.raises(ValueError,match='explicit_A_game_scope_required'):normalize(tmp_path,workspace='fixture',selected_game_ids=ids)


def test_corrupt_page_and_foreign_workspace_fail(tmp_path):
    export_fixture(tmp_path)
    with pytest.raises(ValueError,match='unsupported_A_input_identity'):normalize(tmp_path,workspace='foreign',selected_game_ids=['game'])
    page=json.loads((tmp_path/'games-000000.json').read_text());page['items'][0]['revision']=2;(tmp_path/'games-000000.json').write_text(json.dumps(page))
    with pytest.raises(ValueError,match='A_page_digest_or_identity_mismatch'):normalize(tmp_path,workspace='fixture',selected_game_ids=['game'])


def test_manifest_identity_cannot_hide_correction(tmp_path):
    manifest=export_fixture(tmp_path);manifest['revision']=2;(tmp_path/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='A_manifest_digest_mismatch'):normalize(tmp_path,workspace='fixture',selected_game_ids=['game'])


def test_missing_delivery_stays_explicitly_unknown(tmp_path):
    export_fixture(tmp_path)
    path=tmp_path/'comparisons-000000.json';document=json.loads(path.read_text());del document['items'][0]['delivery'];path.write_text(json.dumps(document))
    manifest=json.loads((tmp_path/'manifest.json').read_text());next(page for page in manifest['pages'] if page['kind']=='comparisons')['digest']=fingerprint(document)
    manifest['manifest_digest']=fingerprint({key:value for key,value in manifest.items() if key!='manifest_digest'});(tmp_path/'manifest.json').write_text(json.dumps(manifest))
    _,pages=normalize(tmp_path,workspace='fixture',selected_game_ids=['game'])
    assert pages[0]['games'][0]['contexts'][0]['delivery']=={'status':'unknown','reason':'delivery_not_exported'}


def test_C3_original_game_link_and_authored_title_are_translated(tmp_path):
    export_fixture(tmp_path)
    for kind in ['games','routes']:
        path=tmp_path/(kind+'-000000.json');page=json.loads(path.read_text())
        if kind=='games':page['items'][0]['headers']={'Site':'https://www.chess.com/game/live/123'}
        else:page['items'][0]['repertoire_title']='Authored Italian'
        path.write_text(json.dumps(page))
        manifest=json.loads((tmp_path/'manifest.json').read_text());next(item for item in manifest['pages'] if item['kind']==kind)['digest']=fingerprint(page)
        manifest['manifest_digest']=fingerprint({key:value for key,value in manifest.items() if key!='manifest_digest'});(tmp_path/'manifest.json').write_text(json.dumps(manifest))
    manifest,pages=normalize(tmp_path,workspace='fixture',selected_game_ids=['game'])
    assert manifest['selections'][0]['repertoire_title']=='Authored Italian'
    assert pages[0]['games'][0]['game_url']=='https://www.chess.com/game/live/123'


def test_C3_transposed_coverage_disagreement_is_unknown_not_a_merged_gap():
    from tempo_analysis_pilot.inputs import scoped_coverage
    scope={'repertoire_id':'rep','policy_id':'policy','learner_color':'white','fen':chess.STARTING_FEN,
        'complete':True,'revision':1,'covered_replies':['e2e4'],'legal_moves':['e2e4','d2d4'],'evidence_id':'route-one'}
    other={**scope,'covered_replies':['d2d4'],'evidence_id':'route-two'}
    combined=scoped_coverage([scope,other])
    assert len(combined)==1 and combined[0]['complete'] is False
    assert combined[0]['covered_replies']==[]
    assert scoped_coverage([other,scope])==combined
    same={**scope,'evidence_id':'equivalent-route'}
    assert scoped_coverage([scope,same])[0]['complete'] is True
    assert len(scoped_coverage([scope,{**other,'policy_id':'separate-policy'}]))==2


@pytest.mark.parametrize('compatible',[True,False])
@pytest.mark.parametrize('source_metadata',[True,False])
def test_C3_shared_prefix_can_anchor_a_draft_only_with_exact_route_history(tmp_path,compatible,source_metadata):
    export_fixture(tmp_path)
    route_page=json.loads((tmp_path/'routes-000000.json').read_text())
    if source_metadata:route_page['items'][0]['repertoire_title']='Authored white policy'
    duplicate=deepcopy(route_page['items'][0]);duplicate.update(route_id='route-two',route_key='second-route')
    route_page['items'].append(duplicate)
    deviation_page=json.loads((tmp_path/'deviations-000000.json').read_text())
    deviation=deviation_page['items'][0]
    deviation['route_contexts'].append({**deviation['route_contexts'][0],'route_id':'route-two','route_ply':1 if compatible else 2})
    manifest=json.loads((tmp_path/'manifest.json').read_text())
    for kind,page in [('routes',route_page),('deviations',deviation_page)]:
        (tmp_path/(kind+'-000000.json')).write_text(json.dumps(page))
        reference=next(item for item in manifest['pages'] if item['kind']==kind)
        reference.update(digest=fingerprint(page),count=len(page['items']))
        manifest['counts'][kind]=len(page['items'])
    manifest['manifest_digest']=fingerprint({key:value for key,value in manifest.items() if key!='manifest_digest'})
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    _,pages=normalize(tmp_path,workspace='fixture',selected_game_ids=['game'])
    anchor=pages[0]['games'][0]['contexts'][-1]['proposal_anchor']
    if compatible and source_metadata:
        assert anchor=={'source_key':'source','repertoire_key':'white','policy_key':'main','route_key':'e4-e5','anchor_ply':1}
    else:
        assert anchor is None
    deviation['route_contexts'].reverse()
    (tmp_path/'deviations-000000.json').write_text(json.dumps(deviation_page))
    next(item for item in manifest['pages'] if item['kind']=='deviations')['digest']=fingerprint(deviation_page)
    manifest['manifest_digest']=fingerprint({key:value for key,value in manifest.items() if key!='manifest_digest'})
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    _,repeated=normalize(tmp_path,workspace='fixture',selected_game_ids=['game'])
    assert repeated[0]['games'][0]['contexts'][-1]['proposal_anchor']==anchor
