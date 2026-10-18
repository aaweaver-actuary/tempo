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


def test_C3_explicit_physical_execution_mode_keeps_alias_and_interior_owner():
    tables=captured();authority=graphs();moves=['e2e4','e7e5','g1f3','b8c6']
    tables['repertoire_lines'][0].update(trained_color='black',moves_json=json.dumps(moves))
    tables['cards'][0].update(trained_color='black',moves_json=json.dumps(moves))
    authority[0].update(trained_color='black',moves_json=json.dumps(moves))
    raw,manifest,report=translate(tables,authority,workspace='test',repertoire_ids=['italian'],source_key='captured',preserve_physical_execution=True)
    source=json.loads(raw)
    assert len(source['exercises'])==1
    exercise=source['exercises'][0]
    assert exercise['key']=='physical'
    assert exercise['execution_prelude']=={'starting_fen':chess.STARTING_FEN,'moves':['e2e4']}
    assert exercise['specification']['accepted_lines']==[['e7e5','g1f3','b8c6']]
    assert manifest['legacy_aliases']=={'physical':'physical'} and manifest['historical_mapping_unknown']==[]
    assert source['bindings']==[{'route_key':'line','policy_key':'italian','ply':1,'exercise_key':'physical'},
        {'route_key':'line','policy_key':'italian','ply':3,'exercise_key':'physical','exercise_ply':2}]
    assert report['historical_interior_binding_count']==1 and report['historical_prelude_count']==1


@pytest.mark.parametrize('equivalent_start', [
    chess.STARTING_FEN.rsplit(' ', 2)[0] + ' 9 23',
    'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1',
])
def test_C3_policy_authority_uses_canonical_start_position(equivalent_start):
    tables=captured()
    base=chess.Board(equivalent_start)
    if base.turn == chess.WHITE:
        first=['e2e4','e7e5','g1f3','b8c6','f1c4']
        second=first[:-1]+['f1b5']
        original=chess.STARTING_FEN
    else:
        first=['e7e5','g1f3','b8c6','f1c4']
        second=first[:-1]+['f1b5']
        original=base.fen(en_passant='legal')
    tables['repertoire_lines'][0].update(start_fen=original,moves_json=json.dumps(first))
    tables['repertoires'].append({'id':'ruy','name':'Ruy Lopez'})
    tables['repertoire_lines'].append({'id':'ruy-route','repertoire_id':'ruy','trained_color':'white',
        'start_fen':equivalent_start,'moves_json':json.dumps(second)})
    raw,_,_=run(tables)
    route=json.loads(raw)['routes'][0]
    assert route['authority_start_ply']==len(first)
    assert route['starting_fen']==original
    assert route['moves']==first


def test_C3_canonical_policy_root_retains_turn_castling_and_legal_en_passant():
    from tempo_a_bridge.legacy import policy_root_key
    assert policy_root_key(chess.STARTING_FEN)!=policy_root_key(chess.STARTING_FEN.replace(' w ', ' b '))
    assert policy_root_key(chess.STARTING_FEN)!=policy_root_key(chess.STARTING_FEN.replace(' KQkq ', ' - '))
    board=chess.Board()
    for move in ['e2e4','a7a6','e4e5','d7d5']:
        board.push_uci(move)
    assert board.has_legal_en_passant()
    with_ep=board.fen(en_passant='legal')
    assert policy_root_key(with_ep)!=policy_root_key(with_ep.replace(' d6 ', ' - '))


def test_C3_identical_canonical_routes_do_not_choose_an_arbitrary_policy():
    tables=captured()
    tables['repertoires'].append({'id':'alternative','name':'Alternative policy'})
    other={**tables['repertoire_lines'][0], 'id':'other-route','repertoire_id':'alternative',
        'start_fen':chess.STARTING_FEN.rsplit(' ',2)[0]+' 4 20'}
    tables['repertoire_lines'].append(other)
    raw,_,_=run(tables)
    assert json.loads(raw)['routes'][0]['authority_start_ply'] is None
