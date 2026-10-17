import json
from copy import deepcopy
import chess
import pytest
from tempo_a_bridge.legacy import translate


def captured():
    return {'repertoires':[{'id':'italian','name':'Italian'}],
        'repertoire_lines':[{'id':'line','repertoire_id':'italian','trained_color':'white','start_fen':chess.STARTING_FEN,'moves_json':'["e2e4","e7e5","g1f3"]'}],
        'cards':[{'id':'physical','start_fen':chess.STARTING_FEN,'moves_json':'["e2e4","e7e5","g1f3"]','trained_color':'white','revision':1,'archived':0,'pending_validation':0}],
        'repertoire_cards':[{'repertoire_id':'italian','card_id':'physical'}]}


def graphs():
    return [{'line_id':'line','repertoire_id':'italian','card_id':'physical','starting_fen':chess.STARTING_FEN,'moves_json':'["e2e4","e7e5","g1f3"]','trained_color':'white'}]


def run(tables=None, authority=None):
    return translate(tables or captured(),authority or graphs(),workspace='test',repertoire_ids=['italian'],source_key='captured')


def test_C3_translation_preserves_full_physical_executable_and_has_no_reentry():
    raw, manifest, report = run()
    source=json.loads(raw)
    assert next(item for item in source['exercises'] if item['key']=='physical')['specification']['accepted_lines']==[['e2e4','e7e5','g1f3']]
    assert manifest['legacy_aliases']=={'physical':'physical'}
    assert manifest['reentry']==[]
    assert [item for item in source['bindings'] if item['exercise_key']=='physical']==[{'route_key':'line','policy_key':'italian','ply':0,'exercise_key':'physical'}]
    assert any(item['ply']==2 and item['exercise_key'].startswith('authored:') for item in source['bindings'])
    assert report['diagnostics']==[]


@pytest.mark.parametrize('field,value,reason',[
    ('trained_color',None,'historical_color_unresolved'),('revision',2,'historical_revision_requires_explicit_successor'),
    ('moves_json','["d2d4"]','historical_executable_changed'),('archived',1,'historical_card_unavailable')])
def test_C3_unresolved_history_cannot_authorize_a_historical_alias(field,value,reason):
    tables=captured();tables['cards'][0][field]=value
    raw,manifest,report=run(tables)
    assert all(item['exercise_key'].startswith('authored:') for item in json.loads(raw)['bindings'])
    assert manifest['legacy_aliases']=={}
    assert report['diagnostics'][0]['reason']==reason
    assert len(json.loads(raw)['routes'])==1


def test_C3_translation_is_deterministic_and_never_uses_names_as_identity():
    tables=captured();first=run(tables)
    tables['repertoires'][0]['name']='Renamed'
    second=run(tables)
    assert json.loads(first[0])['exercises']==json.loads(second[0])['exercises']
    assert first[1]['legacy_aliases']==second[1]['legacy_aliases']
    assert run()==run()


def test_C3_incorrect_route_cannot_authorize_fen_only_reentry():
    tables=captured();tables['repertoire_lines'][0]['moves_json']='["d2d4","d7d5","c1f4"]'
    raw,manifest,report=run(tables)
    assert all(item['exercise_key'].startswith('authored:') for item in json.loads(raw)['bindings'])
    assert report['diagnostics'][0]['reason']=='exact_route_binding_unresolved'


def test_C3_black_prefix_starting_on_white_turn_cannot_be_an_alias():
    tables=captured();tables['repertoire_lines'][0]['trained_color']='black';tables['cards'][0]['trained_color']='black'
    authority=graphs();authority[0]['trained_color']='black'
    raw,manifest,report=run(tables,authority)
    assert next(item for item in json.loads(raw)['bindings'] if item['exercise_key']=='normalized:physical')['ply']==1
    assert manifest['legacy_aliases']=={}
    assert 'normalized:physical' in manifest['historical_mapping_unknown']
    assert report['diagnostics'][0]['reason']=='opponent_prelude_historical_adoption_unsupported'


def test_C3_policy_anchor_uses_full_capture_not_only_selected_repertoire():
    tables=captured()
    tables['repertoires'].append({'id':'ruy','name':'Ruy Lopez'})
    tables['repertoire_lines'][0]['moves_json']='["e2e4","e7e5","g1f3","b8c6","f1c4"]'
    tables['repertoire_lines'].append({'id':'ruy-route','repertoire_id':'ruy','trained_color':'white','start_fen':chess.STARTING_FEN,'moves_json':'["e2e4","e7e5","g1f3","b8c6","f1b5"]'})
    raw,_,report=run(tables)
    assert json.loads(raw)['routes'][0]['authority_start_ply']==5
    assert report['excluded_repertoires']==['ruy']
