import json
from copy import deepcopy
import chess
import pytest
from app.services.frozen_maia_plan import create_plan
from app.services.opening_segmentation import decision_identity


def snapshot():
    initial=chess.STARTING_FEN
    cards=[{'id':'target','revision':1,'repertoire_id':'policy','content_type':'opening','kind':'prefix','start_fen':initial,'moves_json':'["e2e4","e7e5","g1f3"]','trained_color':'white','state':'new','introduced_at':None,'due_date':'2026-10-14','archived':0,'pending_validation':0,'superseded_by':None,'source_ref':None,'source_fen':None}]
    result={name:[] for name in ('memberships','reviews','attempts','observations','origins','queue','blocks','deleted','priorities','generated','priority_publications','gameplay','publications','presented_teaching','presentations')}
    result.update({'metadata':[{'database':'tempo','schema':'public','schema_version':47,'maia_elo':1500,'study_only_mode':1}], 'cutoff':'2026-10-14T12:00:00+00:00','cards':cards,'repertoires':[{'id':'policy','name':'Fixture','created_at':'today'}],
       'lines':[{'id':'line','repertoire_id':'policy','created_at':'today','trained_color':'white','start_fen':initial,'moves_json':cards[0]['moves_json']}],
       'graphs':[{'card_id':'target','repertoire_id':'policy','line_id':'line','decision_index':0}]})
    config={'scenarios':[{'name':'Fixture','repertoire_id':'policy','color':'white','prefix':['e4'],'weight':1}]}
    return result,config


def fake_maia(fen):
    legal=[move.uci() for move in chess.Board(fen).legal_moves]
    return [{'move_uci':move,'probability':.6 if move=='e7e5' else .4/(len(legal)-1)} for move in legal]


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
