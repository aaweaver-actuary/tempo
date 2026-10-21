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
            'backend/app/services/frozen_maia_plan.py':'1'*64,'database/tooling/frozen-maia-order.py':'1'*64}}


def plan_cli():
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('frozen_cli',Path(__file__).parents[2]/'database/tooling/frozen-maia-order.py')
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    return cli


def refresh_checksum(plan):
    from app.services.opening_segmentation import stable_key
    plan['checksum']=stable_key({key:value for key,value in plan.items() if key not in ('checksum','run_id')})
    plan['run_id']=plan['checksum']


def uniform_maia(fen):
    legal=list(chess.Board(fen).legal_moves)
    return [{'move_uci':move.uci(),'probability':1/len(legal)} for move in legal]


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
    spec=importlib.util.spec_from_file_location('frozen_cli',Path(__file__).parents[2]/'database/tooling/frozen-maia-order.py')
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
    spec=importlib.util.spec_from_file_location('frozen_cli',Path(__file__).parents[2]/'database/tooling/frozen-maia-order.py')
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
    spec=importlib.util.spec_from_file_location('frozen_cli',Path(__file__).parents[2]/'database/tooling/frozen-maia-order.py')
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


@pytest.mark.parametrize('identifier',['scored-000','deferred-000'])
def test_uncertain_legacy_origin_is_protected_even_after_unadmission(identifier):
    data,config=localized_tail_snapshot(scored_count=199,deferred_count=1)
    # An old admission can outlive queue trimming and introduced_at reset. It
    # cannot prove that the pre-issuance-tracking card was never presented.
    data['origins']=[{'card_id':identifier,'issued_as_head':0,'attempt_failed':0,
        'review_result_json':None,'last_status':'queued','legacy':1}]
    plan=create_plan(data,config,fake_maia,provenance(),allow_unscored_tail=True)
    protected=next(row for row in plan['inventory'] if row['id']==identifier)
    assert protected['classification']=='not_admitted'
    assert protected['final_disposition']=='protected'
    assert 'uncertain_legacy_exposure' in protected['reasons']
    assert identifier not in {row['id'] for row in plan['ranks']}
    assert plan['activation_safe']


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
    spec=importlib.util.spec_from_file_location('frozen_cli',Path(__file__).parents[2]/'database/tooling/frozen-maia-order.py')
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


def test_unrooted_transposition_defers_other_answers_and_represented_descendants():
    data,config=snapshot()
    known_moves=['e2e4','e7e5','g1f3','b8c6','f1b5']
    data['cards'][0]['moves_json']=json.dumps(known_moves)
    data['lines'][0]['moves_json']=json.dumps(known_moves)
    descendant=chess.Board()
    for move in known_moves[:-1]:descendant.push_uci(move)
    data['cards'].append({**data['cards'][0],'id':'descendant','start_fen':descendant.fen(),'moves_json':'["f1b5"]'})
    missing=chess.Board()
    for move in ['e2e4','e7e5','g1f3','b8c6','f3g1']:missing.push_uci(move)
    data['lines'].append({**data['lines'][0],'id':'unrooted-transposition',
        'start_fen':missing.fen(),'moves_json':'["c6b8","b1c3"]'})
    plan=create_plan(data,config,uniform_maia,provenance(),allow_unscored_tail=True)
    assert {row['id'] for row in plan['deferred_cards']}=={'target','descendant'}
    assert not plan['activation_safe']  # Both cards exceed the unchanged 1% bound.
    diagnostic=plan['lineage_diagnostics'][0]
    assert diagnostic['affected_card_ids']==['descendant','target']
    assert all('unknown_route_mass' in row['reason_codes'] for row in plan['deferred_cards'])
    assert plan['diagnostic_summary']['affected_scored_card_ids']==[]


def test_required_scenario_with_no_route_to_its_anchor_is_fatal():
    data,config=snapshot()
    config['scenarios'][0]['weight']=.5
    config['scenarios'].append({'name':'Missing anchor','repertoire_id':'other','color':'white','prefix':['d4'],'weight':.5})
    data['repertoires'].append({'id':'other','name':'Missing anchor','created_at':'today'})
    data['lines'].append({**data['lines'][0],'id':'outside-condition','repertoire_id':'other'})
    plan=create_plan(data,config,uniform_maia,provenance(),allow_unscored_tail=True)
    assert not plan['activation_safe']
    assert 'other' in plan['scenario_failures']


def test_uncertain_arrival_at_conditioning_anchor_does_not_change_known_scores():
    data,config=snapshot()
    config['scenarios'][0]['prefix']=['e4','e5']
    known_moves=['e2e4','e7e5','g1f3','b8c6','f1b5']
    data['cards'][0]['moves_json']=json.dumps(known_moves)
    data['lines'][0]['moves_json']=json.dumps(known_moves)
    baseline=create_plan(data,config,uniform_maia,provenance())
    missing=chess.Board()
    for move in ['e2e4','e7e5','g1f3','b8c6','f3g1']:missing.push_uci(move)
    data['lines'].append({**data['lines'][0],'id':'unrooted-arrival',
        'start_fen':missing.fen(),'moves_json':'["c6b8"]'})
    plan=create_plan(data,config,uniform_maia,provenance(),allow_unscored_tail=True)
    assert plan['activation_safe'] and plan['deferred_count']==0
    assert plan['lineage_diagnostics'][0]['affected_card_ids']==[]
    assert [(row['id'],row['marginal_score']) for row in plan['ranks']]==[(row['id'],row['marginal_score']) for row in baseline['ranks']]


def test_apply_requires_the_reviewed_uncertainty_policy():
    data,config=localized_tail_snapshot()
    plan=create_plan(data,config,fake_maia,provenance(),allow_unscored_tail=True)
    del plan['lineage_uncertainty_policy']
    refresh_checksum(plan)
    with pytest.raises(ValueError,match='uncertainty policy'):
        plan_cli().validate_plan(plan)


@pytest.mark.parametrize('fraction',[0, float('nan')])
def test_apply_recomputes_deferred_fraction_instead_of_trusting_metadata(fraction):
    data,config=localized_tail_snapshot(scored_count=99,deferred_count=2)
    plan=create_plan(data,config,fake_maia,provenance(),allow_unscored_tail=True)
    plan.update(activation_safe=True,deferred_fraction=fraction)
    refresh_checksum(plan)
    with pytest.raises(ValueError,match='[Dd]eferred.*(fraction|bound)'):
        plan_cli().validate_plan(plan)


def test_apply_rejects_a_tail_that_disagrees_with_baseline_order():
    data,config=localized_tail_snapshot()
    plan=create_plan(data,config,fake_maia,provenance(),allow_unscored_tail=True)
    plan['ranks'][-2:]=reversed(plan['ranks'][-2:])
    ranks_by_id={row['id']:index for index,row in enumerate(plan['ranks'],1)}
    for collection in ('ranks','inventory','deferred_cards'):
        for row in plan[collection]:
            if row['id'] in ranks_by_id:row['rank']=ranks_by_id[row['id']]
    refresh_checksum(plan)
    with pytest.raises(ValueError,match='[Dd]eterministic|[Bb]aseline'):
        plan_cli().validate_plan(plan)


@pytest.mark.parametrize('change',['exposed_scored','deleted_deferred','nonlocal_reason','rank_definition','missing_scenario'])
def test_apply_rechecks_rank_safety_and_evidence_even_with_a_valid_checksum(change):
    data,config=localized_tail_snapshot()
    plan=create_plan(data,config,fake_maia,provenance(),allow_unscored_tail=True)
    inventory_by_id={row['id']:row for row in plan['inventory']}
    if change=='exposed_scored':
        inventory_by_id[plan['ranks'][0]['id']].update(classification='already_studied',reasons=['already_shown_attempted_reviewed'])
    elif change=='deleted_deferred':
        inventory_by_id[plan['ranks'][-1]['id']]['reasons'].append('deleted')
    elif change=='nonlocal_reason':
        inventory_by_id[plan['ranks'][-1]['id']]['reasons'].append('inference_not_run')
    elif change=='rank_definition':
        plan['ranks'][0]['moves_json']='["d2d4"]'
    elif change=='missing_scenario':
        plan['decision_evidence'].clear()
    refresh_checksum(plan)
    with pytest.raises(ValueError):plan_cli().validate_plan(plan)


def test_lineage_summary_counts_distinct_cards_and_source_lines(tmp_path):
    data,config=localized_tail_snapshot(scored_count=199,deferred_count=1)
    data['lines'].append({**data['lines'][-1],'id':'second-missing-line'})
    plan=create_plan(data,config,fake_maia,provenance(),allow_unscored_tail=True)
    summary=plan['diagnostic_summary']
    assert summary['source_line_count']==2 and summary['affected_card_count']==1
    assert summary['unknown_route_mass_card_ids']==['deferred-000']
    assert summary['affected_scenarios']==[{'repertoire_id':'policy','scenario':'Fixture','affected_card_count':1,
        'scored_count':0,'deferred_count':1,'excluded_count':0,'protected_count':0}]
    plan_cli().export(plan,tmp_path)
    assert json.loads((tmp_path/'diagnostics.json').read_text())['summary']==summary


def test_reader_capability_is_checksum_bound_and_old_or_missing_markers_are_rejected():
    from app.services.frozen_maia_math import READER_VERSION, VERSION
    data,config=snapshot()
    plan=create_plan(data,config,fake_maia,provenance())
    assert VERSION=='frozen-maia-v1'
    assert plan['required_reader_version']==READER_VERSION=='frozen-maia-v2'
    plan_cli().validate_plan(plan)
    for marker in (None,'frozen-maia-v1'):
        incompatible=deepcopy(plan)
        if marker is None:
            del incompatible['required_reader_version']
        else:
            incompatible['required_reader_version']=marker
        refresh_checksum(incompatible)
        with pytest.raises(ValueError,match='reader capability'):
            plan_cli().validate_plan(incompatible)


def test_capture_uses_explicit_view_key_sources_and_never_falls_back_to_all_columns():
    class Cursor:
        def __init__(self,rows):self.rows=rows
        def fetchall(self):return self.rows
    class Catalog:
        def __init__(self,keys):self.keys=keys;self.calls=[]
        def execute_native(self,statement,parameters):
            self.calls.append((statement,parameters))
            return Cursor([(key,) for key in self.keys])
    cli=plan_cli()
    catalog=Catalog(['repertoire_id','generation'])
    assert cli._capture_key_columns(catalog,'current_repertoire_card_priority_generations')==['repertoire_id','generation']
    assert catalog.calls[0][1]==('repertoire_card_priority_generations',)
    assert 'WITH ORDINALITY' in catalog.calls[0][0] and 'information_schema' not in catalog.calls[0][0]
    missing=Catalog([])
    with pytest.raises(ValueError,match='No stable primary key.*current_gameplay_card_priorities'):
        cli._capture_key_columns(missing,'current_gameplay_card_priorities')
    assert len(missing.calls)==1


@pytest.mark.parametrize('invalid_key_order',[False,True])
def test_capture_uses_database_collation_for_composite_pagination_keys(monkeypatch,invalid_key_order):
    from contextlib import contextmanager

    cli=plan_cli()
    source_rows=[{'id':f'a{index:03d}','sequence':1} for index in range(126)]
    source_rows.extend([{'id':'a125','sequence':2},{'id':'a125','sequence':10},
                        {'id':'__captured_tactics__','sequence':1},
                        {'id':'__tactics__','sequence':1}])

    class Cursor:
        def __init__(self,rows):self.rows=rows
        def fetchall(self):return self.rows
        def fetchone(self):return self.rows[0]

    class LocaleDatabase:
        def __init__(self):self.page_statements=[]
        def execute_native(self,statement,parameters=()):
            if 'system_identifier' in statement:
                return Cursor([{'database':'tempo','schema':'public'}])
            raise AssertionError(statement)
        def execute(self,statement,parameters=()):
            if 'CROSS JOIN settings' in statement:return Cursor([])
            self.page_statements.append(statement)
            key=lambda row:(row['id'].strip('_'),row['sequence'])
            ordered=sorted(source_rows,key=key)
            if len(parameters)>1:
                previous={'id':parameters[0],'sequence':parameters[1]}
                ordered=[row for row in ordered if key(row)>key(previous)]
            return Cursor([(json.dumps(row),not invalid_key_order) for row in ordered[:parameters[-1]]])

    database=LocaleDatabase()
    @contextmanager
    def reader():yield database

    monkeypatch.setattr(cli,'TABLES',{'repertoires':'repertoires'})
    monkeypatch.setattr(cli,'_capture_key_columns',lambda database,table:['id','sequence'])
    if invalid_key_order:
        with pytest.raises(ValueError,match='Repeated or nonadvancing pagination key'):
            cli._capture_once(reader)
        return
    captured=cli._capture_once(reader)
    assert captured['repertoires']==sorted(source_rows,key=lambda row:(row['id'].strip('_'),row['sequence']))
    assert len(database.page_statements)==2
    assert all('ORDER BY "id","sequence" LIMIT ?' in statement
               for statement in database.page_statements)
    assert '("id","sequence")>(?,?)' in database.page_statements[1]
    assert all('(LAG("id") OVER capture_order,LAG("sequence") OVER capture_order)' in statement
               and 'WINDOW capture_order AS (ORDER BY "id","sequence")' in statement
               and 'COLLATE' not in statement for statement in database.page_statements)


def test_frozen_cli_waits_for_admission_without_holding_a_connection_or_replaying_writes(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from app.services.redis_admission_gate import BackgroundAdmissionDeferred

    cli=plan_cli()
    state={'attempts':0,'held':False,'writes':0,'clock':0.0}
    @contextmanager
    def connection():
        state['attempts']+=1
        if state['attempts']<3:raise BackgroundAdmissionDeferred('Waiting for foreground activity')
        state['held']=True
        try:yield 'database'
        finally:state['held']=False
    def sleep(seconds):
        assert not state['held']
        state['clock']+=seconds
    monkeypatch.setattr(cli,'background_connection',connection)
    monkeypatch.setattr(cli,'time',SimpleNamespace(monotonic=lambda:state['clock'],sleep=sleep))
    writer=getattr(cli,'_ordering_write_connection',cli.background_connection)
    with writer() as database:
        assert database=='database' and state['held']
        state['writes']+=1
    assert state['attempts']==3 and state['writes']==1 and not state['held']


def test_frozen_cli_admission_wait_times_out_without_opening_a_write(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from app.services.redis_admission_gate import BackgroundAdmissionDeferred

    cli=plan_cli()
    state={'clock':0.0,'attempts':0,'writes':0}
    @contextmanager
    def connection():
        state['attempts']+=1
        raise BackgroundAdmissionDeferred('Waiting for foreground activity')
        yield  # The failed admission must never reach a database body.
    monkeypatch.setattr(cli,'background_connection',connection)
    monkeypatch.setattr(cli,'time',SimpleNamespace(monotonic=lambda:state['clock'],
        sleep=lambda seconds:state.update(clock=state['clock']+seconds)))
    with pytest.raises(BackgroundAdmissionDeferred):
        with cli._ordering_write_connection(timeout_seconds=.2):state['writes']+=1
    assert state['clock']==pytest.approx(.2) and state['attempts']==3 and state['writes']==0


def test_frozen_cli_does_not_replay_a_write_body_that_raises_admission_error(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from app.services.redis_admission_gate import BackgroundAdmissionDeferred

    cli=plan_cli()
    state={'attempts':0,'writes':0,'closed':0}
    @contextmanager
    def connection():
        state['attempts']+=1
        try:yield 'database'
        finally:state['closed']+=1
    def unexpected_sleep(seconds):raise AssertionError('A started write must not be retried')
    monkeypatch.setattr(cli,'background_connection',connection)
    monkeypatch.setattr(cli,'time',SimpleNamespace(monotonic=lambda:0,sleep=unexpected_sleep))
    with pytest.raises(BackgroundAdmissionDeferred,match='body failure'):
        with cli._ordering_write_connection():
            state['writes']+=1
            raise BackgroundAdmissionDeferred('body failure')
    assert state=={'attempts':1,'writes':1,'closed':1}


def test_apply_rejects_unexplained_exclusion_of_an_otherwise_rankable_card():
    data,config=snapshot()
    plan=create_plan(data,config,fake_maia,provenance())
    plan['inventory'][0].update(final_disposition='excluded',status='excluded',reasons=['unknown_route_mass'])
    plan.update(ranks=[],scored_count=0,target_candidate_count=0,excluded_count=1)
    refresh_checksum(plan)
    with pytest.raises(ValueError,match='Unexplained exclusion'):
        plan_cli().validate_plan(plan)
