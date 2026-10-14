import json
import csv
from copy import deepcopy
import chess
import pytest
from app.services.frozen_maia_plan import create_plan
from app.services.opening_segmentation import decision_identity


def snapshot():
    initial=chess.STARTING_FEN
    cards=[{'id':'target','revision':1,'repertoire_id':'policy','content_type':'opening','kind':'prefix','start_fen':initial,'moves_json':'["e2e4","e7e5","g1f3"]','trained_color':'white','state':'new','introduced_at':None,'due_date':'2026-10-14','archived':0,'pending_validation':0,'superseded_by':None,'source_ref':None,'source_fen':None}]
    result={name:[] for name in ('memberships','reviews','attempts','observations','origins','queue','blocks','deleted','priorities','generated','priority_publications','gameplay','publications','presented_teaching','presentations')}
    result.update({'metadata':[{'cluster_id':'fixture-cluster','database_oid':'16384','database':'tempo','schema':'public','schema_version':47,'maia_elo':1500,'study_only_mode':1}], 'cutoff':'2026-10-14T12:00:00+00:00','cards':cards,'repertoires':[{'id':'policy','name':'Fixture','created_at':'today'}],
       'lines':[{'id':'line','repertoire_id':'policy','created_at':'today','trained_color':'white','start_fen':initial,'moves_json':cards[0]['moves_json']}],
       'graphs':[{'card_id':'target','repertoire_id':'policy','line_id':'line','decision_index':0}]})
    config={'scenarios':[{'name':'Fixture','repertoire_id':'policy','color':'white','prefix':['e4'],'weight':1}],
        'expected_database':'tempo','expected_schema':'public','maia_elo_override':None}
    return result,config


def fake_maia(fen):
    legal=[move.uci() for move in chess.Board(fen).legal_moves]
    return [{'move_uci':move,'probability':.6 if move=='e7e5' else .4/(len(legal)-1)} for move in legal]


def localized_tail_snapshot(scored_count=198, deferred_count=2):
    data,config=snapshot()
    original=data['cards'][0]
    scored=[{**original,'id':f'scored-{index:03d}'} for index in range(scored_count)]
    data['cards']=scored
    data['graphs']=[{'card_id':card['id'],'repertoire_id':'policy','line_id':'line','decision_index':0} for card in scored]
    board=chess.Board();board.push_uci('e2e4');board.push_uci('a7a6')
    missing_fen=board.fen()
    for index in range(deferred_count):
        identifier=f'deferred-{index:03d}'
        data['cards'].append({**original,'id':identifier,'start_fen':missing_fen,'moves_json':'["g1f3"]'})
        line_id=f'missing-line-{index:03d}'
        data['lines'].append({'id':line_id,'repertoire_id':'policy','created_at':'today','trained_color':'white','start_fen':missing_fen,'moves_json':'["g1f3"]'})
        data['graphs'].append({'card_id':identifier,'repertoire_id':'policy','line_id':line_id,'decision_index':0})
    return data,config


def provenance():
    model_files={'scripts/maia-inference.mjs','app/lib/all_moves_maia3.json','app/lib/all_moves_maia3_reversed.json',
        *(f'public/maia3/parts/part-{index:02d}' for index in range(6))}
    return {'rating_self':1500,'rating_opponent':1500,'model':{'checksums':{path:'0'*64 for path in model_files},'runtime':'fixture','node':'v26'},
        'implementation_checksums':{'backend/app/services/frozen_maia_math.py':'1'*64,
            'backend/app/services/frozen_maia_plan.py':'1'*64,'scripts/frozen-maia-order.py':'1'*64}}


def test_real_plan_partial_teaching_covers_only_the_evidenced_decision():
    data,config=snapshot()
    studied={**data['cards'][0],'id':'studied','moves_json':'["e2e4"]'}
    data['cards'].append(studied)
    data['presentations']=[{**studied,'id':1,'card_id':'studied','captured_at':'2026-10-13'}]
    data['presented_teaching']=[{'card_id':'studied','revision':1,'ply':0}]
    plan=create_plan(data,config,fake_maia)
    assert plan['complete']
    assert len(plan['ranks'])==1
    assert plan['ranks'][0]['marginal_score']==pytest.approx(.6)
    assert decision_identity('policy',chess.STARTING_FEN,'white','e2e4') in plan['covered_decisions']


def test_inventory_classifies_queued_unstarted_and_uncertain_legacy_exposure():
    data,config=snapshot();data['cards'][0]['state']='learning';data['cards'][0]['introduced_at']='2026-10-14'
    data['queue']=[{'card_id':'target','status':'queued','attempt_state':'clean','attempt_failed':0,'review_result_json':None}]
    plan=create_plan(data,config,fake_maia)
    assert plan['inventory'][0]['classification']=='admitted_unstarted'
    assert plan['complete']
    data['origins']=[{'card_id':'target','issued_as_head':0,'attempt_failed':0,'review_result_json':None,'last_status':'queued','legacy':1}]
    plan=create_plan(data,config,fake_maia)
    assert not plan['complete'] and 'uncertain_legacy_exposure' in plan['inventory'][0]['reasons']


def test_missing_exact_lineage_is_unknown_and_blocks_applyable_plan():
    data,config=snapshot();data['lines']=[]
    # Mapping is still verified by a valid sibling source, which doesn't teach target Nf3.
    data['lines']=[{'id':'sibling','repertoire_id':'policy','created_at':'today','trained_color':'white','start_fen':chess.STARTING_FEN,'moves_json':'["e2e4"]'}]
    plan=create_plan(data,config,fake_maia)
    assert not plan['complete'] and not plan['ranks']
    assert any(reason.startswith('missing_exact_card_lineage') for reason in plan['inventory'][0]['reasons'])


def test_archived_superseded_pending_and_attempted_cards_are_never_activation_targets():
    data,config=snapshot()
    for field,value in [('archived',1),('superseded_by','replacement'),('pending_validation',1)]:
        altered=deepcopy(data);altered['cards'][0][field]=value
        assert not create_plan(altered,config,fake_maia)['ranks']
    data['attempts']=[{'card_id':'target','attempt_id':'shown','state':'active','manifest_json':'{"decisions":[]}'}]
    assert not create_plan(data,config,fake_maia)['ranks']


def test_effective_fen_cache_reuses_counter_variants_and_binds_model_identity(tmp_path):
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('frozen_cli',Path(__file__).parents[2]/'scripts/frozen-maia-order.py')
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    inference=object.__new__(cli.Inference)
    inference.rating=1500;inference.directory=tmp_path;inference.provenance={'model':'fixture'}
    inference.calls=0;inference.memory={};inference.foreground_url=None;inference.next_gate_check=0
    requests=[]
    def request(value):
        requests.append(value)
        legal=list(chess.Board(value['fen']).legal_moves)
        moves=[{'move_uci':move.uci(),'probability':1/len(legal)} for move in legal]
        return {'id':value['id'],'moves':moves,'provenance':inference.provenance}
    inference.request=request
    first=inference(chess.STARTING_FEN)
    assert inference(chess.STARTING_FEN.replace('0 1','7 4'))==first
    assert len(requests)==1
    inference.memory={};assert inference(chess.STARTING_FEN)==first
    assert len(requests)==1
    inference.provenance={'model':'replacement'};inference(chess.STARTING_FEN)
    assert len(requests)==2
    assert not list(tmp_path.glob('*.pending'))


def test_existing_card_prefix_recovers_partial_source_without_game_tree_search():
    from app.services.frozen_maia_plan import recover_lines
    board=chess.Board();board.push_uci('e2e4')
    partial={'id':'partial','repertoire_id':'policy','start_fen':board.fen(),'moves_json':'["e7e5","g1f3"]'}
    prefix={'repertoire_id':'policy','start_fen':chess.STARTING_FEN,'moves_json':'["e2e4"]'}
    lines,diagnostics=recover_lines([partial],[prefix])
    assert not diagnostics and json.loads(lines[0]['moves_json'])==['e2e4','e7e5','g1f3']
    assert lines[0]['recovered_prefix']==['e2e4']


def test_invalid_prefix_evidence_cannot_supply_an_ancestry_guess():
    from app.services.frozen_maia_plan import recover_lines
    board=chess.Board();board.push_uci('e2e4')
    partial={'id':'partial','repertoire_id':'policy','start_fen':board.fen(),'moves_json':'["e7e5","g1f3"]'}
    invalid={'repertoire_id':'policy','start_fen':chess.STARTING_FEN,'moves_json':'["e2e4","e2e5"]'}
    lines,diagnostics=recover_lines([partial],[invalid])
    assert not lines and diagnostics[0]['reason']=='Missing initial/anchor lineage'


def test_capture_revalidation_rejects_concurrent_study_changes(monkeypatch):
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('frozen_cli',Path(__file__).parents[2]/'scripts/frozen-maia-order.py')
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    captures=iter([{'cutoff':'first','reviews':[]},{'cutoff':'second','reviews':[{'card_id':'studied'}]}])
    monkeypatch.setattr(cli,'_capture_once',lambda factory:next(captures))
    with pytest.raises(ValueError,match='Incoherent capture'):cli.capture()
    captures=iter([{'cutoff':'first','reviews':[]},{'cutoff':'second','reviews':[]}])
    assert cli.capture()=={'cutoff':'first','reviews':[]}


def test_source_starting_at_anchor_needs_no_pre_anchor_move_history():
    from app.services.frozen_maia_plan import recover_lines
    from app.services.opening_segmentation import opening_position_key
    board=chess.Board();board.push_uci('e2e4')
    source={'id':'anchor-source','repertoire_id':'policy','start_fen':board.fen(),'moves_json':'["e7e5","g1f3"]'}
    recovered,diagnostics=recover_lines([source],anchor_positions={'policy':opening_position_key(board.fen())})
    assert recovered==[source] and not diagnostics


def test_allow_unscored_tail_ranks_localized_unknown_cards_after_scored_backlog():
    data,config=localized_tail_snapshot()
    studied={**data['cards'][0],'id':'previously-studied'}
    data['cards'].append(studied)
    data['reviews']=[{'card_id':'previously-studied','source_kind':'study','invalidated_at':None,'reviewed_at':'2026-10-13'}]
    plan=create_plan(data,config,fake_maia,provenance(),allow_unscored_tail=True)

    assert plan['scoring_policy']=='allow_unscored_tail'
    assert plan['activation_safe'] and not plan['probability_complete']
    assert plan['scored_count']==198 and plan['deferred_count']==2
    assert [row['id'] for row in plan['ranks'][:3]]==['scored-000','scored-001','scored-002']
    assert [row['id'] for row in plan['ranks'][-2:]]==['deferred-000','deferred-001']
    assert [row['rank'] for row in plan['ranks']]==list(range(1,201))
    assert all(row['marginal_score'] is None and row['scoring_status']=='unscored_deferred' for row in plan['deferred_cards'])
    assert all(row['diagnostic_refs'] for row in plan['deferred_cards'])
    assert plan['inventory'][-1]['final_disposition']=='excluded'
    assert plan['deferred_fraction']==pytest.approx(.01)
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('frozen_cli',Path(__file__).parents[2]/'scripts/frozen-maia-order.py')
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    cli.validate_plan(plan)


def test_deferred_tail_is_deterministic_when_snapshot_rows_are_shuffled():
    from random import Random
    data,config=localized_tail_snapshot()
    shuffled=deepcopy(data)
    random=Random(272)
    for name in ('cards','lines','graphs','memberships'):
        random.shuffle(shuffled[name])
    first=create_plan(data,config,fake_maia,provenance(),allow_unscored_tail=True)
    second=create_plan(shuffled,config,fake_maia,provenance(),allow_unscored_tail=True)
    assert [(row['id'],row['rank']) for row in first['ranks']]==[(row['id'],row['rank']) for row in second['ranks']]


def test_strict_mode_still_rejects_the_same_unknown_cards():
    data,config=localized_tail_snapshot(scored_count=3,deferred_count=2)
    plan=create_plan(data,config,fake_maia,provenance())
    assert not plan['complete'] and not plan['probability_complete'] and not plan['activation_safe']
    assert plan['scoring_policy']=='strict' and plan['deferred_count']==0


def test_multiple_lineage_diagnostics_reference_one_deferred_card_once():
    data,config=localized_tail_snapshot(scored_count=199,deferred_count=1)
    repeated={**data['lines'][-1],'id':'second-missing-line'}
    data['lines'].append(repeated)
    data['graphs'].append({'card_id':'deferred-000','repertoire_id':'policy','line_id':repeated['id'],'decision_index':0})
    plan=create_plan(data,config,fake_maia,provenance(),allow_unscored_tail=True)
    card=next(row for row in plan['deferred_cards'] if row['id']=='deferred-000')
    assert plan['deferred_count']==1
    assert len(card['diagnostic_refs'])==2
    assert len([row for row in plan['ranks'] if row['id']=='deferred-000'])==1


def test_missing_source_ancestry_marks_otherwise_reachable_card_unknown_route_mass():
    data,config=localized_tail_snapshot(scored_count=199,deferred_count=1)
    plan=create_plan(data,config,fake_maia,provenance(),allow_unscored_tail=True)
    deferred=next(row for row in plan['deferred_cards'] if row['id']=='deferred-000')
    assert 'unknown_route_mass' in deferred['reason_codes']
    diagnostic=next(item for item in plan['lineage_diagnostics'] if item['line_id']=='missing-line-000')
    assert diagnostic['affected_card_ids']==['deferred-000']
    assert diagnostic['scenario']=='Fixture' and diagnostic['decision_ids']


def test_uncertain_legacy_exposure_is_protected_not_appended_to_tail():
    data,config=localized_tail_snapshot(scored_count=199,deferred_count=1)
    target=next(row for row in data['cards'] if row['id']=='deferred-000')
    target.update({'introduced_at':'2026-10-14','state':'learning'})
    data['queue']=[{'card_id':'deferred-000','status':'queued','attempt_state':'clean','attempt_failed':0,'review_result_json':None}]
    data['origins']=[{'card_id':'deferred-000','issued_as_head':0,'attempt_failed':0,'review_result_json':None,'last_status':'queued','legacy':1}]
    plan=create_plan(data,config,fake_maia,provenance(),allow_unscored_tail=True)
    assert plan['activation_safe']
    assert 'deferred-000' not in {row['id'] for row in plan['ranks']}
    protected=next(row for row in plan['inventory'] if row['id']=='deferred-000')
    assert protected['final_disposition']=='protected'


def test_scenario_wide_inference_failure_or_tail_over_one_percent_blocks_activation():
    data,config=localized_tail_snapshot(scored_count=99,deferred_count=2)
    def failed_maia(_fen):
        raise RuntimeError('model unavailable')
    failed=create_plan(data,config,failed_maia,provenance(),allow_unscored_tail=True)
    assert not failed['activation_safe'] and failed['scenario_failures']
    assert failed['deferred_fraction']>failed['deferred_limit']
    bounded=create_plan(data,config,fake_maia,provenance(),allow_unscored_tail=True)
    assert not bounded['activation_safe'] and bounded['deferred_fraction']>0.01


def test_adding_unscored_card_does_not_perturb_scored_order():
    base,config=snapshot()
    baseline=create_plan(base,config,fake_maia,provenance())
    expanded,_=localized_tail_snapshot(scored_count=1,deferred_count=1)
    expanded['cards'][0]={**base['cards'][0]}
    expanded['graphs'][0]['card_id']=base['cards'][0]['id']
    expanded['cards'][0]['id']=base['cards'][0]['id']
    expanded_plan=create_plan(expanded,config,fake_maia,provenance(),allow_unscored_tail=True)
    assert [row['id'] for row in baseline['ranks']]==[base['cards'][0]['id']]
    assert [row['id'] for row in expanded_plan['ranks'] if row['scoring_status']=='scored']==[base['cards'][0]['id']]


def test_export_keeps_zero_marginal_score_distinct_from_unknown(tmp_path):
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('frozen_cli',Path(__file__).parents[2]/'scripts/frozen-maia-order.py')
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    data,config=localized_tail_snapshot(scored_count=1,deferred_count=1)
    data['observations']=[{'decision_id':decision} for decision in create_plan(data,config,fake_maia)['inventory'][0]['decisions']]
    plan=create_plan(data,config,fake_maia,provenance(),allow_unscored_tail=True)
    cli.export(plan,tmp_path)
    rows=list(csv.DictReader((tmp_path/'ranks.csv').open()))
    zero=next(row for row in rows if row['scoring_status']=='scored')
    unknown=next(row for row in rows if row['scoring_status']=='unscored_deferred')
    assert float(zero['marginal_score'])==0.0
    assert unknown['marginal_score']=='' and unknown['final_disposition']=='deferred'
    assert (tmp_path/'deferred-cards.csv').exists()
