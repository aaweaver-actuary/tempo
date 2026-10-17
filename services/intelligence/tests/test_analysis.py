"""C pure regressions. Corpus is explicitly synthetic, not A/B acceptance."""
from copy import deepcopy
import json
from pathlib import Path
import chess
import pytest
from tempo_intelligence.analysis.models import Manifest, Page
from tempo_intelligence.analysis.classify import source_findings, ordered_positions, compare_scores, explorer_coverage, maia_distribution
from tempo_intelligence.analysis.engine import comparison, validate_result

FIXTURES=Path(__file__).resolve().parent.parent/'fixtures/analysis'

def corpus():
    manifest=Manifest.model_validate_json((FIXTURES/'manifest.json').read_text()).model_dump(mode='json')
    games=Page.model_validate_json((FIXTURES/'games-0.json').read_text()).model_dump(mode='json')['games']
    return manifest,games

def variation(cp=0,depth=20,kind='centipawns',move='e2e4'):
    return {'rank':1,'moves':[move],'score':{'kind':kind,'value':cp},'score_perspective':'side_to_move','depth':depth}

def test_C_accepted_alternative():
    manifest,games=corpus();game=games[2]
    game['contexts'][0].update(status='missed',card_id='card',decision_id='decision')
    assert source_findings(manifest,[game])[0]==[]

def test_C_course_difference_not_blunder():
    manifest,games=corpus();findings,_=source_findings(manifest,[games[0]])
    assert [f['category'] for f in findings]==['repertoire_miss']
    assert not compare_scores(variation(20),variation(0))['significant']

def test_C_unknown_not_gap():
    manifest,games=corpus();manifest['selections'][0]['lineage']['activation_status']='unknown'
    assert source_findings(manifest,games)[0]==[]
    assert source_findings(manifest,games)[1]
    manifest,games=corpus();games[1]['contexts'][1]['route_established']=False
    assert not any(f['category']=='practical_gap' for f in source_findings(manifest,games)[0])

def test_C_actual_opponent_gap():
    manifest,games=corpus();findings,_=source_findings(manifest,games)
    gap=next(f for f in findings if f['category']=='practical_gap')
    assert (gap['representative']['game_id'],gap['representative']['ply'],gap['opponent_move'])==('gap',2,'a7a6')
    assert gap['engine'] is None and gap['supporting_game_count']==1

def test_C_post_gap_no_cascade():
    manifest,games=corpus()
    assert len([f for f in source_findings(manifest,games)[0] if f['category']=='practical_gap'])==1

def test_C_explicit_reentry():
    manifest,games=corpus();games[1]['contexts'][2]['reentry_authorized']=True
    assert len([f for f in source_findings(manifest,games)[0] if f['category']=='practical_gap'])==2

def test_C_distinct_game_dedup():
    manifest,games=corpus();games[1]['contexts'].append(deepcopy(games[1]['contexts'][1]))
    findings=source_findings(manifest,games+deepcopy(games))[0]
    assert all(f['supporting_game_count']==1 for f in findings)

def test_C_policy_independence():
    manifest,games=corpus();before=source_findings(manifest,games)[0]
    extra=deepcopy(manifest['selections'][0]);extra.update(repertoire_id='unrelated',policy_id='unrelated')
    manifest['selections'].append(extra)
    assert source_findings(manifest,games)[0]==before

def test_C_partial_explorer():
    evidence={'total_games':100,'moves':[{'uci':'e2e4','games':40}],'unallocated_games':60}
    assert explorer_coverage(evidence,['e2e4'])=={'lower':0.4,'upper':1.0,'status':'bounded'}
    assert explorer_coverage({**evidence,'total_games':0},[])['status']=='unknown'

def test_C_maia_full_legal_set():
    with pytest.raises(ValueError,match='whole_position'):
        maia_distribution({'legal_moves':['e2e4']},{'predictions':[{'move':'e2e4','probability':1.0}]},['e2e4','d2d4'])
    assert maia_distribution({'legal_moves':['e2e4']},{'predictions':[{'move':'e2e4','probability':1.0}]},['e2e4'])=={'e2e4':1.0}

def test_C_engine_played_move():
    root={'variations':[variation(200)],'engine_version':'synthetic','provenance':{'runtime_name':'synthetic','runtime_version':'test','implementation_version':'test'}}
    assert comparison(root,None,'d2d4')['status']=='played_analysis_pending'
    assert comparison(root,{**root,'variations':[variation(0,move='d2d4')]},'d2d4')['significant']

def test_C_engine_perspective():
    from tempo_intelligence.analysis.classify import positions
    for color,moves in [('white',['e2e4','e7e5']),('black',['e2e4','e7e5'])]:
        game={'game_id':color,'source_revision':1,'source_digest':'test','starting_fen':chess.STARTING_FEN,'learner_color':color,'moves':moves}
        position=list(positions(game))[0]
        board=chess.Board(position['starting_fen'])
        for move in position['history']:board.push_uci(move)
        assert board.turn==(color=='white')
        assert board.fen(en_passant='fen')==position['fen']
        assert compare_scores(variation(200),variation(0))['loss_cp']==200

def test_C_mate_not_cp():
    assert compare_scores(variation(3,kind='mate'),variation(200))['significant']
    assert compare_scores(variation(0),variation(-3,kind='mate'))['significant']
    result=compare_scores(variation(3,kind='mate'),variation(5,kind='mate'))
    assert not result['significant'] and 'loss_cp' not in result

@pytest.mark.parametrize('best,played,significant',[(99,0,False),(100,0,True),(101,0,True),(0,10,False)])
def test_C_engine_quality(best,played,significant):
    assert compare_scores(variation(best),variation(played))['significant']==significant
    assert not compare_scores(variation(best,17),variation(played,17))['significant']
    assert not compare_scores(variation(best,20),variation(played,19))['significant']

def test_C_partial_scan_truth():
    manifest,games=corpus();items=ordered_positions(games)
    assert len(items)>2 and len({(p['game_id'],p['ply']) for p in items})==len(items)
    assert {p['game_id'] for p in items[:3]}=={g['game_id'] for g in games}

@pytest.mark.parametrize('change',[{'revision':True},{'schema_version':2},{'workspace_id':''}])
def test_analysis_input_strict_versions(change):
    manifest,_=corpus()
    with pytest.raises(ValueError):Manifest.model_validate({**manifest,**change})

def test_source_specific_proactive_gap_and_policy_identity():
    from tempo_intelligence.analysis.sources import source_advice
    from datetime import datetime,timezone
    manifest,_=corpus();board=chess.Board();board.push_uci('e2e4')
    manifest['coverage_scopes']=[{'repertoire_id':'rep','policy_id':'policy','learner_color':'white','fen':board.fen(),'complete':True,'revision':1,'covered_replies':['e7e5'],'legal_moves':[m.uci() for m in board.legal_moves],'evidence_id':'scope'}]
    evidence={'evidence_id':'explorer','accepted_at':datetime.now(timezone.utc),'descriptor':{'kind':'lichess','position_key':' '.join(board.fen().split()[:4]),'parameters':{'ratings':'1600'}},'payload':{'total_games':200,'unallocated_games':100,'moves':[{'uci':'c7c5','games':100}]}}
    results=source_advice(manifest,[evidence]);assert len(results)==1
    assert results[0]['gap_kind']=='proactive' and results[0]['supporting_game_count']==0
    assert results[0]['source_evidence'][0]['frequency']==0.5 and results[0]['source_evidence'][0]['coverage']['status']=='bounded'
    evidence['descriptor']['kind']='masters'
    assert source_advice(manifest,[evidence])[0]['source_evidence'][0]['kind']=='masters'
    manifest['coverage_scopes'][0]['complete']=False
    assert source_advice(manifest,[evidence])==[]

def test_packaged_analysis_v1_schemas_match_public_models():
    from tempo_intelligence import analysis_v1
    import tempo_intelligence
    directory=Path(tempo_intelligence.__file__).parent/'analysis/schemas/v1'
    for name in ['Manifest','Page','RunRequest','Command','Finding','ReportPage']:
        assert json.loads((directory/(name+'.schema.json')).read_text())==getattr(analysis_v1,name).model_json_schema()

def test_analysis_browser_caller_cannot_forge_source_activation():
    from tempo_intelligence import Configuration,create_app
    from fastapi.testclient import TestClient
    class ReadStore:
        workspace_id='fixture'
        def validate_startup(self):pass
    configuration=Configuration(workspace_id='fixture',database_url='postgresql://unused/intelligence',caller_token='c'*40,operator_token='o'*40,worker_token='w'*40,worker_enabled=False,origins=('http://localhost:4100',))
    with TestClient(create_app(configuration,store=ReadStore())) as client:
        caller={'Authorization':'Bearer '+'c'*40,'Origin':'http://localhost:4100'}
        assert client.get('/v1/analysis/bootstrap',headers=caller).json()['workspace_id']=='fixture'
        assert client.post('/v1/analysis/inputs',headers=caller,json={}).status_code==401
        assert client.options('/v1/analysis/bootstrap',headers={'Origin':'http://localhost:4100','Access-Control-Request-Method':'GET','Access-Control-Request-Headers':'authorization,x-tempo-workspace'}).status_code==200


def test_ranking_requires_exact_selected_population():
    from tempo_intelligence import analysis_v1
    with pytest.raises(ValueError,match='explicit_ranking_evidence_required'):
        analysis_v1.RunRequest(schema_version=1,workspace_id='fixture',run_id='run',revision=1,input_id='input',input_revision=1,ranking_source='lichess')
    request=analysis_v1.RunRequest(schema_version=1,workspace_id='fixture',run_id='run',revision=1,input_id='input',input_revision=1,ranking_source='lichess',source_evidence_ids=['cohort'],ranking_evidence_id='cohort')
    assert request.ranking_evidence_id=='cohort'


def test_worker_session_is_closed_before_durable_failure():
    from tempo_intelligence.analysis.worker import work
    from tempo_intelligence.errors import DomainError
    events=[]
    class Owner:
        workspace='fixture'
        def derive(self,run):pass
        def claim(self,run,builder):return {'job':{'id':'owned'}}
        def failure(self,claim,**values):events.append(('failure',values['code']))
    class Client:
        build_job=None
        def execute(self,job):raise ConnectionError('synthetic transport loss')
        def close(self):events.append(('close',None))
    with pytest.raises(DomainError,match='analysis_worker_failed'):work(Owner(),'run',1,client=Client())
    assert events[:2]==[('close',None),('failure','worker_session_lost')]


def test_worker_acknowledged_cancellation_is_not_a_failed_search():
    from tempo_intelligence.analysis.worker import work
    from tempo_intelligence.errors import DomainError
    events=[]
    class Cancelled(Exception):
        retryable=False
        reason='cancelled'
    class Owner:
        workspace='fixture'
        def derive(self,run):pass
        def claim(self,run,builder):return {'job':{}}
        def failure(self,claim,**values):events.append(values)
    class Client:
        build_job=None
        def execute(self,job):raise Cancelled()
        def close(self):pass
    with pytest.raises(DomainError,match='analysis_worker_failed'):work(Owner(),'run',1,client=Client())
    assert events[0]['code']=='cancelled' and not events[0]['retryable']


def test_positive_opponent_encounters_supply_distinct_game_denominator():
    manifest,games=corpus();gap=deepcopy(games[1]);covered=deepcopy(gap);covered['game_id']='covered-encounter';covered['contexts']=[]
    board=chess.Board(gap['starting_fen']);board.push_uci(gap['moves'][0])
    encounter={'ply':1,'repertoire_id':'rep','policy_id':'policy','fen':board.fen(en_passant='fen'),'route_ids':['route'],'scope_complete':True,'scope_revision':1,'evidence_id':'synthetic-encounter'}
    for game in (gap,covered):game.update(opponent_encounters_complete=True,opponent_encounters=[encounter,encounter])
    finding=next(item for item in source_findings(manifest,[gap,covered])[0] if item['category']=='practical_gap')
    assert finding['eligible_encounter_games']==2 and finding['actual_frequency']==0.5
    covered['opponent_encounters_complete']=False
    finding=next(item for item in source_findings(manifest,[gap,covered])[0] if item['category']=='practical_gap')
    assert finding['eligible_encounter_games'] is None and 'eligible_encounter_denominator_unavailable' in finding['uncertainty']


def test_C3_original_game_url_and_repertoire_title_survive_findings():
    manifest,games=corpus()
    games[0]['game_url']='https://www.chess.com/game/live/123'
    manifest['selections'][0]['repertoire_title']='Authored Italian'
    finding=source_findings(manifest,[games[0]])[0][0]
    assert finding['representative']['game_url']=='https://www.chess.com/game/live/123'
    assert finding['repertoire_title']=='Authored Italian'


def test_C3_original_physical_learning_card_is_distinct_from_content_identity():
    from tempo_intelligence.analysis.read_models import Finding
    manifest,games=corpus()
    context=games[0]['contexts'][0]
    context['learning_card_id']='original-physical-card'
    finding=source_findings(manifest,[games[0]])[0][0]
    assert finding['learning_card_id']=='original-physical-card'
    assert finding['card_id']==context['card_id']
    metadata={'finding_revision':1,'evidence_revision':'sha256:'+'a'*64,'input_id':'input','input_revision':1}
    assert Finding.model_validate({**finding,**metadata}).learning_card_id=='original-physical-card'
    # Absent metadata never changes accepted historical input/report bytes.
    del context['learning_card_id']
    historical=source_findings(manifest,[games[0]])[0][0]
    assert 'learning_card_id' not in historical
    assert 'learning_card_id' not in Finding.model_validate({**historical,**metadata}).model_dump(mode='json')


def test_C3_absent_optional_metadata_preserves_original_input_page_digest():
    from tempo_intelligence.analysis.models import Page
    from tempo_intelligence.identity import digest
    root=Path(__file__).parents[1]/'fixtures/analysis'
    original=json.loads((root/'games-0.json').read_text())
    assert Page.model_validate(original).model_dump(mode='json')==original
    assert digest(Page.model_validate(original).model_dump(mode='json'))==digest(original)
