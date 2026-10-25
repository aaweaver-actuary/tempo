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


def interior_capture(prefix=None, continuation=None):
    prefix = prefix or ['e2e4','d7d5','e4d5','d8d5','b1c3','d5a5','d2d4']
    continuation = continuation or ['c8f5','d1f3']
    board = chess.Board()
    for move in prefix:
        board.push_uci(move)
    tables, authority = captured(), graphs()
    tables['repertoire_lines'][0]['moves_json'] = json.dumps(prefix + continuation)
    for row in (tables['cards'][0], authority[0]):
        row['moves_json'] = json.dumps(continuation)
    tables['cards'][0]['start_fen'] = authority[0]['starting_fen'] = board.fen(en_passant='legal')
    return tables, authority


@pytest.mark.parametrize('variant', ['noncapturable_ep', 'counters', 'graph_encoding'])
def test_physical_interior_binding_uses_legal_position_without_rewriting_identity(variant):
    tables, authority = interior_capture()
    if variant == 'counters':
        tables['cards'][0]['start_fen'] = authority[0]['starting_fen'] = (
            tables['cards'][0]['start_fen'].rsplit(' ',2)[0] + ' 17 42')
    if variant == 'graph_encoding':
        authority[0]['starting_fen'] = authority[0]['starting_fen'].replace(' - ', ' d3 ')
    original = deepcopy(tables['cards'][0])
    raw, manifest, report = translate(tables, authority, workspace='test', repertoire_ids=['italian'],
        source_key='captured', preserve_physical_execution=True)
    assert report['mapped_identities'] == ['physical']
    assert manifest['legacy_aliases'] == {'physical':'physical'}
    source = json.loads(raw)
    assert next(item for item in source['bindings'] if item['exercise_key']=='physical')['ply'] == 8
    exercise = next(item for item in source['exercises'] if item['key']=='physical')
    assert exercise['execution_prelude'] == {'starting_fen':original['start_fen'], 'moves':['c8f5']}
    assert exercise['specification']['accepted_lines'] == [['d1f3']]
    assert tables['cards'][0] == original


@pytest.mark.parametrize('fault', ['capturable_ep', 'turn', 'castling', 'executable', 'revision'])
def test_physical_position_equivalence_retains_rights_and_executable_authority(fault):
    tables, authority = interior_capture(['e2e4','a7a6','e4e5','d7d5'], ['e5e6'])
    card = tables['cards'][0]
    if fault == 'capturable_ep':
        card['start_fen'] = authority[0]['starting_fen'] = card['start_fen'].replace(' d6 ', ' - ')
    if fault == 'turn':
        card['start_fen'] = authority[0]['starting_fen'] = card['start_fen'].replace(' w ', ' b ')
    if fault == 'castling':
        card['start_fen'] = authority[0]['starting_fen'] = card['start_fen'].replace(' KQkq ', ' - ')
    if fault == 'executable': authority[0]['moves_json'] = '["e5d6"]'
    if fault == 'revision': card['revision'] = 2
    _, manifest, report = run(tables, authority)
    assert manifest['legacy_aliases'] == {} and report['mapped_identities'] == []


def test_equivalent_repeated_route_positions_cannot_choose_a_physical_root():
    tables, authority = captured(), graphs()
    tables['repertoire_lines'][0]['moves_json'] = '["g1f3","g8f6","f3g1","f6g8","g1f3"]'
    tables['cards'][0]['moves_json'] = authority[0]['moves_json'] = '["g1f3"]'
    _, manifest, report = run(tables, authority)
    assert manifest['legacy_aliases'] == {}
    assert report['diagnostics'][0]['reason'] == 'exact_route_binding_unresolved'


@pytest.mark.parametrize('source', ['card','graph'])
def test_invalid_physical_fen_is_not_repaired_into_execution_equivalence(source):
    tables, authority = interior_capture()
    row, key = (tables['cards'][0], 'start_fen') if source=='card' else (authority[0], 'starting_fen')
    row[key] = row[key].replace(' - ', ' e3 ')
    assert not chess.Board(row[key]).is_valid()
    _, manifest, report = run(tables, authority)
    assert manifest['legacy_aliases'] == {} and report['mapped_identities'] == []


def published_graphs(tables):
    # Explicit invented accepted segments for these synthetic inputs, not a
    # production rebuild/export helper. Real steps come from captured rows.
    result=[]
    for line in tables['repertoire_lines']:
        board=chess.Board(line['start_fen']);keys=[];moves=json.loads(line['moves_json'])
        for move in moves:
            if board.turn==(line['trained_color']=='white'):
                keys.append(' '.join(board.fen(en_passant='legal').split()[:4]))
            board.push_uci(move)
        if keys:
            result.append({'repertoire_id':line['repertoire_id'],'generation':1,'line_id':line['id'],
                'decision_index':0,'card_id':'saved-graph:'+line['id'],'parent_card_id':None,
                'starting_fen':line['start_fen'],'moves_json':line['moves_json'],'trained_color':line['trained_color'],
                'decision_fen_key':keys[-1],'segment_kind':'prefix','first_decision_index':0,
                'last_decision_index':len(keys)-1,'decision_fen_keys_json':json.dumps(keys)})
    return result


def graph_only(tables=None, steps=None, selected=None):
    tables = tables if tables is not None else captured()
    return translate(tables, steps if steps is not None else published_graphs(tables),
        workspace='fresh', repertoire_ids=selected or ['italian'], source_key='fresh-graph', graph_only=True)


def test_graph_only_fresh_source_needs_no_card_or_history_tables_and_has_no_aliases():
    tables = captured()
    del tables['cards'], tables['repertoire_cards']
    before = deepcopy(tables)
    raw, manifest, report = graph_only(tables)
    source = json.loads(raw)
    assert source['repertoires'] == [{'key':'italian','title':'Italian','learner_color':'white'}]
    assert source['routes'] == [{'key':'line','starting_fen':chess.STARTING_FEN,
        'moves':['e2e4','e7e5','g1f3'],'authority_start_ply':0}]
    assert sorted(binding['ply'] for binding in source['bindings']) == [0,2]
    assert all(exercise['key'].startswith('graph:') for exercise in source['exercises'])
    assert manifest['legacy_aliases'] == {} and manifest['historical_mapping_unknown'] == []
    assert report['mapped_identities'] == [] and report['unmapped_authored_decisions'] == {}
    assert report['mode'] == 'graph_only_fresh_start' and report['diagnostics'] == []
    assert tables == before
    assert graph_only(tables) == (raw,manifest,report)


def test_graph_only_ignores_history_revision_color_availability_and_shared_alias_gates():
    tables = captured()
    tables['cards'][0].update(revision=99,trained_color=None,archived=1,pending_validation=1)
    tables['repertoires'].append({'id':'other','name':'Other policy'})
    tables['repertoire_lines'].append({**tables['repertoire_lines'][0],'id':'other-line','repertoire_id':'other'})
    tables['repertoire_cards'].append({'repertoire_id':'other','card_id':'physical'})
    raw,manifest,report = graph_only(tables,selected=['italian','other'])
    source = json.loads(raw)
    assert len(source['repertoires']) == 2 and len(source['routes']) == 2
    assert all(route['authority_start_ply'] is None for route in source['routes'])
    assert manifest['legacy_aliases'] == {} and manifest['historical_mapping_unknown'] == []
    assert report['diagnostics'] == []


def test_graph_only_black_route_preserves_full_opponent_context_with_fresh_learner_definitions():
    tables = captured()
    tables['repertoire_lines'][0].update(trained_color='black',moves_json='["e2e4","e7e5","g1f3","b8c6"]')
    raw,manifest,_ = graph_only(tables)
    source = json.loads(raw)
    assert source['routes'][0]['moves'] == ['e2e4','e7e5','g1f3','b8c6']
    assert sorted(binding['ply'] for binding in source['bindings']) == [1,3]
    assert [exercise['specification']['accepted_lines'] for exercise in source['exercises']] != []
    assert all(chess.Board(exercise['starting_fen']).turn == chess.BLACK for exercise in source['exercises'])
    assert manifest['legacy_aliases'] == {}


def test_graph_only_full_catalogue_retains_same_color_policy_authority_boundary():
    tables = captured()
    tables['repertoires'].append({'id':'ruy','name':'Ruy Lopez'})
    tables['repertoire_lines'][0]['moves_json']='["e2e4","e7e5","g1f3","b8c6","f1c4"]'
    tables['repertoire_lines'].append({'id':'ruy-route','repertoire_id':'ruy','trained_color':'white',
        'start_fen':chess.STARTING_FEN,'moves_json':'["e2e4","e7e5","g1f3","b8c6","f1b5"]'})
    raw,_,report = graph_only(tables)
    assert json.loads(raw)['routes'][0]['authority_start_ply'] == 5
    assert report['excluded_repertoires'] == ['ruy']


def test_graph_only_retains_nonserving_names_depth_split_and_empty_repertoire_dispositions():
    tables = captured()
    tables['repertoires'][0]['source_name'] = 'original source'
    tables['repertoires'].append({'id':'empty','name':'Named empty repertoire','source_name':'empty source'})
    tables['repertoire_lines'][0].update(name='Named route',created_at='2030-01-01')
    tables['repertoire_lines'].append({**tables['repertoire_lines'][0],'id':'empty-line','repertoire_id':'empty','moves_json':'[]'})
    tables['repertoire_line_training_depths']=[{'line_id':'line','learner_decision_count':2}]
    tables['prefix_splits']=[{'original_card_id':'physical','source_revision':9}]
    before = deepcopy(tables)
    raw,_,report = graph_only(tables,selected=['italian','empty'])
    assert [row['key'] for row in json.loads(raw)['repertoires']] == ['italian']
    retained = report['non_serving_source_entities']
    assert {'table':'repertoires','key':'empty','fields':['source_name'],'reason':'no_nonempty_authored_route'} in retained
    assert {'table':'repertoire_lines','key':'line','fields':['created_at','name'],'reason':'raw_source_metadata'} in retained
    assert {'table':'prefix_splits','row_count':1,'reason':'raw_graph_closure_metadata'} in retained
    assert {'table':'repertoire_line_training_depths','row_count':1,'reason':'raw_graph_closure_metadata'} in retained
    assert report['raw_graph_recovery_copy_required'] is True and tables == before


def test_graph_only_empty_scope_and_mixed_color_never_guess_a_policy():
    tables = captured()
    tables['repertoire_lines'][0]['moves_json']='[]'
    with pytest.raises(ValueError,match='graph_only_nonempty_authored_route_required'):
        graph_only(tables)
    tables = captured()
    tables['repertoire_lines'].append({**tables['repertoire_lines'][0],'id':'black-line','trained_color':'black'})
    with pytest.raises(ValueError,match='mixed_repertoire_policy_color'):
        graph_only(tables)
    with pytest.raises(ValueError,match='graph_only_cannot_adopt_physical_execution'):
        translate(captured(),graphs(),workspace='fresh',repertoire_ids=['italian'],source_key='fresh',graph_only=True,preserve_physical_execution=True)


def canonical_graph_capture():
    tables = captured();steps = published_graphs(tables)
    steps[0]['card_id']='physical'
    tables['cards'][0].update(content_type='opening',canonical_route_source=1,archived=0,
        repertoire_id='italian',revision=99,pending_validation=0)
    tables['repertoire_cards'][0]['canonical_route_source']=1
    return tables,steps


def test_graph_only_canonical_authored_source_uses_explicit_scope_without_history_aliases():
    tables,steps = canonical_graph_capture()
    raw,manifest,report = graph_only(tables,steps)
    assert len(json.loads(raw)['routes']) == 1
    assert manifest['legacy_aliases'] == {} and report['diagnostics'] == []
    assert report['mapped_identities'] == []  # Revision99 is not a historical adoption gate.


@pytest.mark.parametrize('fault,reason',[
    ('missing','published_graph_scope_required'),('foreign-line','graph_line_scope_unresolved'),
    ('different-executable','graph_card_definition_disagrees'),('off-route','graph_ordered_path_unresolved'),
])
def test_graph_only_canonical_card_only_source_cannot_disappear_from_fresh_projection(fault,reason):
    tables,steps = canonical_graph_capture()
    if fault=='missing': steps=[]
    if fault=='foreign-line': steps[0]['line_id']='unknown-line'
    if fault=='different-executable': steps[0]['moves_json']='["d2d4"]'
    if fault=='off-route': tables['repertoire_lines'][0]['moves_json']='["d2d4","d7d5","c1f4"]'
    with pytest.raises(ValueError,match='graph_only_'+reason):
        graph_only(tables,steps)


def test_graph_only_explicit_owner_link_zero_suppresses_canonical_card_fallback():
    tables,steps = canonical_graph_capture()
    tables['repertoire_cards'][0]['canonical_route_source']=0
    steps[0]['card_id']='saved-only-node'
    _,manifest,_ = graph_only(tables,steps)
    assert manifest['legacy_aliases'] == {}
    tables['repertoire_cards']=[]
    with pytest.raises(ValueError,match='canonical_source_occurrence_required'):
        graph_only(tables,steps)


def test_graph_only_cli_uses_embedded_graphs_and_immutable_existing_source_documents(tmp_path,capsys):
    from tempo_a_bridge.legacy import main
    tables,steps = canonical_graph_capture()
    capture=tmp_path/'capture.json'
    capture.write_text(json.dumps({'tables':tables,'graphs':steps}))
    output=tmp_path/'translated'
    arguments=['--capture',str(capture),'--workspace','fresh','--source-key','fresh-graph',
        '--repertoire-id','italian','--output',str(output),'--graph-only']
    main(arguments)
    original={path.name:path.read_bytes() for path in output.iterdir()}
    assert set(original)=={'source.json','import-manifest.json','identity-mapping.json'}
    assert json.loads(original['import-manifest.json'])['legacy_aliases']=={}
    assert json.loads(original['source.json'])['schema_version']==1
    main(arguments)
    assert original=={path.name:path.read_bytes() for path in output.iterdir()}
    tables['repertoires'][0]['name']='Renamed source'
    capture.write_text(json.dumps({'tables':tables,'graphs':steps}))
    with pytest.raises(ValueError,match='immutable_translation_output_conflict'):
        main(arguments)
    assert original=={path.name:path.read_bytes() for path in output.iterdir()}


def test_graph_only_invalid_scope_fails_before_creating_serving_output(tmp_path):
    from tempo_a_bridge.legacy import main
    capture=tmp_path/'capture.json'
    tables,steps=canonical_graph_capture()
    capture.write_text(json.dumps({'tables':tables,'graphs':[]}))
    output=tmp_path/'not-created'
    with pytest.raises(ValueError,match='published_graph_scope_required'):
        main(['--capture',str(capture),'--workspace','fresh','--source-key','fresh',
            '--repertoire-id','italian','--output',str(output),'--graph-only'])
    assert not output.exists()


def segmented_graph_capture():
    tables=captured()
    moves=['e2e4','e7e5','g1f3','b8c6','f1b5','a7a6']
    tables['repertoire_lines'][0]['moves_json']=json.dumps(moves)
    tables['repertoire_line_training_depths']=[{'line_id':'line','learner_decision_count':2}]
    tables['deleted_cards']=[]
    tables['opening_graph_publications']=[{'repertoire_id':'italian','generation':4,'state':'ready'}]
    all_keys=json.loads(published_graphs(tables)[0]['decision_fen_keys_json'])
    prefix={**published_graphs(tables)[0],'generation':4,'card_id':'saved-prefix',
        'moves_json':json.dumps(moves[:3]),'last_decision_index':1,
        'decision_fen_keys_json':json.dumps(all_keys[:2]),'decision_fen_key':all_keys[1]}
    board=chess.Board()
    for move in moves[:3]: board.push_uci(move)
    descendant={**prefix,'decision_index':1,'card_id':'saved-descendant','parent_card_id':'saved-prefix',
        'starting_fen':board.fen(en_passant='fen'),'moves_json':json.dumps(moves[3:5]),
        'segment_kind':'decision','first_decision_index':2,'last_decision_index':2,
        'decision_fen_keys_json':json.dumps(all_keys[2:]),'decision_fen_key':all_keys[2]}
    return tables,[prefix,descendant]


def test_graph_only_preserves_published_depth_split_segment_and_does_not_regenerate_unserved_decisions():
    tables,steps=segmented_graph_capture()
    raw,manifest,report=graph_only(tables,steps[:1])
    source=json.loads(raw)
    assert source['routes'][0]['moves']==['e2e4','e7e5','g1f3','b8c6','f1b5','a7a6']
    assert len(source['exercises'])==1
    assert source['exercises'][0]['specification']['accepted_lines']==[['e2e4','e7e5','g1f3']]
    assert [(row['ply'],row.get('exercise_ply',0)) for row in sorted(source['bindings'],key=lambda row:row['ply'])]==[(0,0),(2,2)]
    assert 4 not in [row['ply'] for row in source['bindings']]
    assert report['source_graph_bindings'][0]['card_id']=='saved-prefix'
    assert report['source_graph_bindings'][0]['generation']==4
    assert manifest['legacy_aliases']=={}


def test_graph_only_tombstone_excludes_saved_node_without_recreating_it_from_the_full_route():
    tables,steps=segmented_graph_capture()
    tables['deleted_cards']=[{'card_id':'saved-prefix','deleted_at':'2030-01-01'}]
    raw,_,report=graph_only(tables,steps)
    source=json.loads(raw)
    assert len(source['exercises'])==1 and [row['ply'] for row in source['bindings']]==[4]
    exercise=source['exercises'][0]
    assert exercise['execution_prelude']['moves']==['b8c6']
    assert exercise['specification']['accepted_lines']==[['f1b5']]
    assert report['excluded_graph_occurrences']==[{'repertoire_id':'italian','generation':4,'line_id':'line',
        'decision_index':0,'card_id':'saved-prefix','parent_card_id':None,'reason':'captured_deleted_card'}]


@pytest.mark.parametrize('fault,reason',[
    ('generation','published_generation_unresolved'),('decision-keys','decision_keys_disagree'),
    ('learner-range','learner_scope_unresolved'),('overlap','learner_coverage_overlaps'),
])
def test_graph_only_does_not_reinterpret_changed_published_scope(fault,reason):
    tables,steps=segmented_graph_capture()
    if fault=='generation': steps[0]['generation']=3
    if fault=='decision-keys': steps[0]['decision_fen_keys_json']='[]'
    if fault=='learner-range': steps[0]['last_decision_index']=20
    if fault=='overlap': steps.append({**steps[0],'decision_index':2,'card_id':'another-overlapping-card'})
    with pytest.raises(ValueError,match='graph_only_graph_'+reason if fault!='generation' else 'graph_only_'+reason):
        graph_only(tables,steps)


@pytest.mark.parametrize('field,value', [('archived',1),('pending_validation',1),('superseded_by','other-card')])
def test_graph_only_source_availability_disagreement_is_not_a_schedule_reset(field,value):
    tables,steps=canonical_graph_capture()
    tables['cards'][0][field]=value
    error='no_serving_graph_decision' if field=='archived' else 'graph_card_availability_disagrees'
    with pytest.raises(ValueError,match=error):
        graph_only(tables,steps)


@pytest.mark.parametrize('color',['white','black'])
def test_graph_only_null_physical_color_uses_recorded_same_route_graph_color_without_rewriting_raw_source(color):
    tables,steps=canonical_graph_capture()
    if color=='black':
        moves=['e2e4','e7e5','g1f3','b8c6']
        tables['repertoire_lines'][0].update(trained_color='black',moves_json=json.dumps(moves))
        tables['cards'][0].update(trained_color='black',moves_json=json.dumps(moves))
        steps=published_graphs(tables);steps[0]['card_id']='physical'
    tables['cards'][0]['trained_color']=None
    before=deepcopy(tables)
    raw,manifest,_=graph_only(tables,steps)
    source=json.loads(raw)
    assert source['repertoires'][0]['learner_color']==color
    assert all(chess.Board(exercise['starting_fen']).turn==(color=='white') for exercise in source['exercises'])
    assert manifest['legacy_aliases']=={} and tables==before
    assert tables['cards'][0]['trained_color'] is None
    tables['cards'][0]['trained_color']='black' if color=='white' else 'white'
    with pytest.raises(ValueError,match='graph_card_definition_disagrees'):
        graph_only(tables,steps)


def test_graph_only_indexed_source_scope_preserves_shared_memberships_explicit_zero_and_null_color_without_aliases():
    tables,steps=canonical_graph_capture()
    tables['cards'][0]['trained_color']=None
    tables['repertoires'].append({'id':'shared-policy','name':'Shared policy'})
    tables['repertoire_lines'].append({**tables['repertoire_lines'][0],'id':'shared-route','repertoire_id':'shared-policy'})
    tables['repertoire_cards'][0]['canonical_route_source']=0
    tables['repertoire_cards'].append({'repertoire_id':'shared-policy','card_id':'physical','canonical_route_source':1})
    steps=[{**steps[0],'repertoire_id':'shared-policy','line_id':'shared-route'}]
    before=deepcopy(tables)
    raw,manifest,report=graph_only(tables,steps,['italian','shared-policy'])
    source=json.loads(raw)
    assert manifest['legacy_aliases']=={} and manifest['historical_mapping_unknown']==[]
    assert report['serving_repertoires']==['shared-policy']
    assert {row['policy_key'] for row in source['bindings']}=={'shared-policy'}
    assert tables==before and tables['cards'][0]['trained_color'] is None
    tables['repertoire_cards'][0]['canonical_route_source']=1
    with pytest.raises(ValueError,match='canonical_source_occurrence_required'):
        graph_only(tables,steps,['italian','shared-policy'])


def generated_dangling_capture():
    tables=captured()
    moves=['e2e4','e7e5','g1f3','b8c6','f1b5','a7a6','b5a4','g8f6','e1g1']
    tables['repertoire_lines'][0]['moves_json']=json.dumps(moves)
    card=tables['cards'][0]
    card.update(moves_json=json.dumps(moves),kind='prefix',content_type='opening',
        canonical_route_source=0,superseded_by='synthetic-absent-successor')
    tables['repertoire_cards'][0]['canonical_route_source']=0
    tables['opening_graph_publications']=[{'repertoire_id':'italian','generation':1,'state':'ready'}]
    tables['prefix_splits']=[];tables['deleted_cards']=[]
    steps=published_graphs(tables);steps[0]['card_id']='physical'
    return tables,steps


def reviewed_dangling_fact(card):
    import hashlib
    row_bytes=json.dumps(card,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
    return {'schema_version':1,'capture_sha256':'a'*64,
        'full_cards_absence_proof':{'report_path':'synthetic-absence-report.json',
            'report_sha256':'b'*64,'stream_script_sha256':'c'*64,'captured_card_count':3},
        'facts':[{'source_card_id':card['id'],'missing_target_card_id':card['superseded_by'],
            'source_record_sha256':hashlib.sha256(row_bytes).hexdigest()}]}


def test_graph_only_dangling_generated_lineage_rebuilt_requires_exact_reviewed_fact_and_preserves_raw_marker():
    tables,steps=generated_dangling_capture()
    before=deepcopy(tables);fact=reviewed_dangling_fact(tables['cards'][0]);fact_before=deepcopy(fact)
    initial_raw,initial_manifest,initial_report=graph_only(tables,steps)
    assert initial_manifest['legacy_aliases']=={}
    assert initial_report['generated_availability_resets'][0]['raw_source_flags']['superseded_by']=='synthetic-absent-successor'
    raw,manifest,report=translate(tables,steps,workspace='fresh',repertoire_ids=['italian'],
        source_key='fresh-graph',graph_only=True,fresh_dangling_lineage=fact)
    source=json.loads(raw)
    assert len(source['exercises'])==1 and len(source['bindings'])==5
    assert source['exercises'][0]['specification']['accepted_lines']==[json.loads(tables['cards'][0]['moves_json'])]
    assert manifest['legacy_aliases']=={} and manifest['historical_mapping_unknown']==[]
    assert any(value.startswith('fresh-lineage-facts:') for value in manifest['provenance']['evidence_refs'])
    assert report['fresh_policy_exceptions']==[{
        'kind':'graph_only_dangling_generated_lineage_rebuilt','source_card_id':'physical',
        'raw_superseded_by':'synthetic-absent-successor','source_record_sha256':fact['facts'][0]['source_record_sha256'],
        'capture_sha256':'a'*64,'full_cards_absence_proof':fact['full_cards_absence_proof']}]
    assert tables==before and fact==fact_before
    report['fresh_policy_exceptions'][0]['full_cards_absence_proof']['report_path']='changed-output-only'
    assert fact==fact_before


@pytest.mark.parametrize('fault',[
    'known-successor','canonical-source','archived','pending','wrong-kind','target-graph-parent',
    'target-split','target-tombstone','not-ready','changed-marker','changed-fingerprint',
])
def test_graph_only_reviewed_dangling_fact_cannot_waive_known_or_conflicting_source(fault):
    tables,steps=generated_dangling_capture();card=tables['cards'][0]
    if fault=='canonical-source': card['canonical_route_source']=1
    if fault=='archived': card['archived']=1
    if fault=='pending': card['pending_validation']=1
    if fault=='wrong-kind': card['kind']='response'
    fact=reviewed_dangling_fact(card)
    if fault=='known-successor': tables['cards'].append({'id':'synthetic-absent-successor'})
    if fault=='target-graph-parent': steps[0]['parent_card_id']='synthetic-absent-successor'
    if fault=='target-split': tables['prefix_splits']=[{'continuation_card_id':'synthetic-absent-successor'}]
    if fault=='target-tombstone': tables['deleted_cards']=[{'card_id':'synthetic-absent-successor'}]
    if fault=='not-ready': tables['opening_graph_publications'][0]['state']='failed'
    if fault=='changed-marker': card['superseded_by']='different-absent-successor'
    if fault=='changed-fingerprint': card['revision']=2
    with pytest.raises(ValueError,match='graph_only_'):
        translate(tables,steps,workspace='fresh',repertoire_ids=['italian'],source_key='fresh',
            graph_only=True,fresh_dangling_lineage=fact)


def test_graph_only_reviewed_dangling_fact_still_requires_complete_ordered_graph_path():
    tables,steps=generated_dangling_capture();fact=reviewed_dangling_fact(tables['cards'][0])
    steps[0]['last_decision_index']=3
    with pytest.raises(ValueError,match='graph_ordered_path_unresolved'):
        translate(tables,steps,workspace='fresh',repertoire_ids=['italian'],source_key='fresh',
            graph_only=True,fresh_dangling_lineage=fact)
    with pytest.raises(ValueError,match='lineage_facts_require_graph_only'):
        translate(tables,steps,workspace='fresh',repertoire_ids=['italian'],source_key='fresh',fresh_dangling_lineage=fact)


@pytest.mark.parametrize('fault',['extra-approval','multiple-facts','invalid-hash','insufficient-count','missing-proof'])
def test_graph_only_lineage_factual_port_is_single_exact_case_not_generic_approval(fault):
    tables,steps=generated_dangling_capture();fact=reviewed_dangling_fact(tables['cards'][0])
    if fault=='extra-approval': fact['approved']=True
    if fault=='multiple-facts': fact['facts'].append(deepcopy(fact['facts'][0]))
    if fault=='invalid-hash': fact['capture_sha256']='not-a-capture-digest'
    if fault=='insufficient-count': fact['full_cards_absence_proof']['captured_card_count']=0
    if fault=='missing-proof': fact.pop('full_cards_absence_proof')
    with pytest.raises(ValueError,match='graph_only_'):
        translate(tables,steps,workspace='fresh',repertoire_ids=['italian'],source_key='fresh',
            graph_only=True,fresh_dangling_lineage=fact)


def test_graph_only_cli_one_reviewed_lineage_fact_is_explicit_and_reported(tmp_path):
    from tempo_a_bridge.legacy import main
    tables,steps=generated_dangling_capture();fact=reviewed_dangling_fact(tables['cards'][0])
    capture=tmp_path/'capture.json';capture.write_text(json.dumps({'tables':tables,'graphs':steps}))
    factual=tmp_path/'fact.json';factual.write_text(json.dumps(fact))
    output=tmp_path/'fresh'
    main(['--capture',str(capture),'--workspace','fresh','--source-key','fresh',
        '--repertoire-id','italian','--output',str(output),'--graph-only','--fresh-dangling-lineage',str(factual)])
    report=json.loads((output/'identity-mapping.json').read_text())
    assert report['fresh_policy_exceptions'][0]['kind']=='graph_only_dangling_generated_lineage_rebuilt'
    assert json.loads((output/'import-manifest.json').read_text())['legacy_aliases']=={}


@pytest.mark.parametrize('role',['second-occurrence','parent'])
def test_graph_only_reviewed_lineage_case_cannot_expand_beyond_one_unparented_unsplit_source(role):
    tables,steps=generated_dangling_capture();fact=reviewed_dangling_fact(tables['cards'][0])
    if role=='second-occurrence':
        tables['repertoire_lines'].append({**tables['repertoire_lines'][0],'id':'second-route'})
        steps.append({**steps[0],'line_id':'second-route'})
    else:
        steps[0]['parent_card_id']='physical'
    with pytest.raises(ValueError,match='graph_card_availability_disagrees'):
        translate(tables,steps,workspace='fresh',repertoire_ids=['italian'],source_key='fresh',
            graph_only=True,fresh_dangling_lineage=fact)


@pytest.mark.parametrize('flags',[
    {'archived':1,'superseded_by':None},
    {'pending_validation':1,'superseded_by':None},
    {'superseded_by':'synthetic-known-successor'},
    {'archived':1,'pending_validation':1,'superseded_by':'synthetic-known-successor'},
])
def test_new_graph_definitions_report_but_do_not_adopt_legacy_generated_availability(flags):
    tables,steps=generated_dangling_capture();tables['cards'][0].update(flags)
    if tables['cards'][0]['superseded_by']=='synthetic-known-successor':
        tables['cards'].append({'id':'synthetic-known-successor'})
    before=deepcopy(tables)
    raw,manifest,report=graph_only(tables,steps)
    assert len(json.loads(raw)['exercises'])==1 and len(json.loads(raw)['bindings'])==5
    assert manifest['legacy_aliases']=={} and report['fresh_policy_exceptions']==[]
    reset=report['generated_availability_resets'][0]
    assert reset['raw_source_flags']=={field:tables['cards'][0].get(field) for field in
        ('canonical_route_source','archived','pending_validation','superseded_by')}
    assert reset['affected_graph_occurrences']==1
    assert tables==before


def test_new_generated_availability_policy_still_requires_captured_ready_publication():
    tables,steps=generated_dangling_capture();tables.pop('opening_graph_publications')
    with pytest.raises(ValueError,match='generated_reset_requires_ready_publication'):
        graph_only(tables,steps)


def test_graph_only_excludes_explicit_authored_archive_definitions_and_exact_learner_ranges_without_dropping_routes():
    tables=captured()
    long_moves=['e2e4','e7e5','g1f3','b8c6','f1b5','a7a6','b5a4','g8f6','e1g1']
    short_moves=['d2d4','d7d5','c2c4','e7e6','b1c3']
    active_moves=['g1f3']
    template=tables['repertoire_lines'][0]
    tables['repertoire_lines']=[{**template,'id':name,'moves_json':json.dumps(sequence)} for name,sequence in
        [('arch-a-1',long_moves),('arch-a-2',long_moves),('arch-a-3',long_moves),('arch-b',short_moves),('active',active_moves)]]
    card_template={**tables['cards'][0],'kind':'prefix','content_type':'opening','canonical_route_source':1,'pending_validation':0}
    tables['cards']=[{**card_template,'id':'arch-a','moves_json':json.dumps(long_moves),'archived':1,'superseded_by':'active'},
        {**card_template,'id':'arch-b','moves_json':json.dumps(short_moves),'archived':1,'superseded_by':None},
        {**card_template,'id':'active','moves_json':json.dumps(active_moves),'archived':0,'superseded_by':None}]
    tables['repertoire_cards']=[{'repertoire_id':'italian','card_id':row['id'],'canonical_route_source':1} for row in tables['cards']]
    tables['opening_graph_publications']=[{'repertoire_id':'italian','generation':1,'state':'ready'}]
    steps=published_graphs(tables)
    for step in steps:
        step['card_id']='arch-a' if step['line_id'].startswith('arch-a') else step['line_id']
    before=deepcopy(tables)
    raw,manifest,report=graph_only(tables,steps);source=json.loads(raw)
    assert len(source['routes'])==5 and len(source['exercises'])==1 and len(source['bindings'])==1
    assert report['authored_archive_excluded_definition_count']==2
    assert report['authored_archive_excluded_occurrence_count']==4
    assert report['authored_archive_excluded_binding_count']==18
    assert {row['reason'] for row in report['excluded_graph_occurrences']}=={'explicit_authored_archive'}
    assert all(row['route_key']=='active' for row in source['bindings'])
    assert manifest['legacy_aliases']=={} and tables==before


def test_graph_only_reused_recorded_node_keeps_source_fen_and_all_raw_clock_occurrences():
    tables,steps=canonical_graph_capture()
    source_fen=chess.STARTING_FEN.rsplit(' ',2)[0]+' 9 23'
    tables['cards'][0]['start_fen']=source_fen
    tables['repertoire_lines'].append({**tables['repertoire_lines'][0],'id':'second-line'})
    steps.append({**steps[0],'line_id':'second-line','starting_fen':chess.STARTING_FEN.rsplit(' ',2)[0]+' 3 7'})
    original_tables=deepcopy(tables);original_steps=deepcopy(steps)
    raw,manifest,report=graph_only(tables,steps);source=json.loads(raw)
    assert len(source['exercises'])==1 and len(source['bindings'])==4
    assert source['exercises'][0]['starting_fen']==source_fen
    assert len({binding['exercise_key'] for binding in source['bindings']})==1
    assert {row['raw_graph_starting_fen'] for row in report['source_graph_bindings']}=={step['starting_fen'] for step in steps}
    assert all(row['recorded_definition_starting_fen']==source_fen for row in report['source_graph_bindings'])
    assert manifest['legacy_aliases']=={} and tables==original_tables and steps==original_steps


@pytest.mark.parametrize('fault',['turn','castling','ep','moves','color'])
def test_generated_availability_reset_cannot_hide_executable_or_position_conflicts(fault):
    tables,steps=generated_dangling_capture();card=tables['cards'][0];card['archived']=1
    if fault=='turn': card['start_fen']=card['start_fen'].replace(' w ',' b ')
    if fault=='castling': card['start_fen']=card['start_fen'].replace(' KQkq ',' - ')
    if fault=='ep': card['start_fen']=card['start_fen'].replace(' - 0 1',' e3 0 1')
    if fault=='moves': card['moves_json']='["d2d4"]'
    if fault=='color': card['trained_color']='black'
    with pytest.raises(ValueError,match='graph_card_definition_disagrees'):
        graph_only(tables,steps)
