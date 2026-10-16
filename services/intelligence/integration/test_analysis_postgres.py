"""C owning PostgreSQL regressions; invoked only by the disposable native runner."""
from copy import deepcopy
import json
from pathlib import Path
import os
from dataclasses import replace
import pytest
import psycopg
from fastapi.testclient import TestClient
from tempo_intelligence.analysis.models import Manifest,Page,RunRequest,Command
from tempo_intelligence.analysis.store import AnalysisStore
from tempo_intelligence.analysis.engine import make_job
from tempo_intelligence.errors import DomainError
from tempo_intelligence.identity import digest
from tempo_intelligence.api import create_app

FIXTURES=Path(__file__).resolve().parent.parent/'fixtures/analysis'

def setup(state,budget=250,run_id='run'):
    store=AnalysisStore(state[0]);manifest=Manifest.model_validate_json((FIXTURES/'manifest.json').read_text());page=Page.model_validate_json((FIXTURES/'games-0.json').read_text())
    store.manifest(manifest);store.page(page);store.finalize(manifest.input_id,Command(command_id='finalize',action='finalize',expected_revision=1))
    store.submit(RunRequest(schema_version=1,workspace_id='fixture',run_id=run_id,revision=1,input_id=manifest.input_id,input_revision=1,position_budget=budget));store.derive(run_id)
    return store

def builder(position,run_id,revision,generation,stage,depth):
    job=make_job(position,run_id,revision,generation,stage,'synthetic-test-profile',1024,depth)
    job['input_digest']=digest({k:v for k,v in job.items() if k not in ('input_digest','execution_generation')})
    return job

def result(claim):
    job=claim['job']
    return {'contract_version':1,**{k:job[k] for k in ('workspace_id','job_id','request_revision','execution_generation','worker','engine_version')},'provenance':{'input_digest':job['input_digest'],'runtime_name':'synthetic','runtime_version':'test','implementation_version':'test','started_at':'2026-10-16T00:00:00Z','finished_at':'2026-10-16T00:00:01Z'},'variations':[{'rank':1,'moves':[job['search_moves'][0] if job['search_moves'] else claim['payload']['actual_move']],'score':{'kind':'centipawns','value':0},'score_perspective':'side_to_move','depth':20}]}

def test_C_duplicate_result(state):
    owner=setup(state);claim=owner.claim('run',builder);r=result(claim)
    assert not owner.accept(claim,r)['replayed'];assert owner.accept(claim,r)['replayed']
    with state[0].transaction(read_only=True) as db:assert db.execute('SELECT count(*) FROM analysis_results').fetchone()['count']==1

def test_C_worker_restart(state):
    owner=setup(state);old=owner.claim('run',builder);owner.failure(old,code='worker_session_lost')
    with state[0].transaction() as db:db.execute("UPDATE analysis_positions SET next_attempt_at=now()-interval '1 second'")
    new=owner.claim('run',builder);assert new['execution_generation']>old['execution_generation']
    with pytest.raises(DomainError,match='stale_analysis_execution'):owner.accept(old,result(old))
    owner.accept(new,result(new))

def test_C_newer_input_during_publish(state):
    owner=setup(state)
    owner.command('run',Command(command_id='continue',action='continue',expected_revision=1,budget=1))
    def invalidate():owner.command('run',Command(command_id='newer',action='continue',expected_revision=1,budget=1))
    assert not owner.derive('run',before_publish=invalidate)
    assert owner.status('run')['requested_generation']>owner.status('run')['published_generation']
    assert owner.derive('run')

def test_C_cancel_resume(state):
    owner=setup(state);claim=owner.claim('run',builder)
    assert owner.command('run',Command(command_id='cancel',action='cancel',expected_revision=1))['state']=='cancel_requested'
    assert owner.claim('run',builder) is None
    with pytest.raises(DomainError,match='stale_analysis_execution'):owner.accept(claim,result(claim))
    owner.failure(claim,code='cancelled');assert owner.status('run')['state']=='cancelled'
    owner.command('run',Command(command_id='resume',action='resume',expected_revision=1))
    assert owner.claim('run',builder)

def test_C_finite_backlog_fairness(state):
    owner=setup(state);seen=[]
    oldest=owner.claim('run',builder);owner.failure(oldest,code='worker_session_lost')
    with state[0].transaction() as db:
        db.execute("UPDATE analysis_positions SET next_attempt_at=now()+interval '1 hour' WHERE position_id=%s",(oldest['position_id'],))
        age=db.execute('SELECT created_at FROM analysis_positions WHERE position_id=%s',(oldest['position_id'],)).fetchone()['created_at']
    for _ in range(20):
        claim=owner.claim('run',builder)
        if not claim:break
        seen.append(claim['position_id']);owner.accept(claim,result(claim))
    assert oldest['position_id'] not in seen
    with state[0].transaction() as db:db.execute("UPDATE analysis_positions SET next_attempt_at=now()-interval '1 second' WHERE position_id=%s",(oldest['position_id'],))
    retry=owner.claim('run',builder);assert retry['position_id']==oldest['position_id'] and retry['created_at']==age
    seen.append(retry['position_id']);owner.accept(retry,result(retry))
    assert len(set(seen))==sum(owner.status('run')['progress'].values())
    assert owner.status('run')['engine_scan_complete']

def test_C_read_has_no_work_effect(state):
    owner=setup(state)
    with state[0].transaction(read_only=True) as db:before=db.execute('SELECT count(*) FROM analysis_executions').fetchone()['count']
    for _ in range(3):owner.report('run');owner.runs();owner.remaining('run')
    with state[0].transaction(read_only=True) as db:assert db.execute('SELECT count(*) FROM analysis_executions').fetchone()['count']==before

def test_C_dismissal_revision(state):
    owner=setup(state);report=owner.report('run');item=report['items'][0]
    command=Command(command_id='dismiss',action='dismiss',expected_revision=item['finding_revision'])
    saved=owner.dismiss('run',item['finding_id'],report['generation'],command)
    assert owner.receipt('dismiss')==saved
    owner.derive('run');updated=owner.report('run')
    assert next(f for f in updated['items'] if f['finding_id']==item['finding_id'])['dismissal_state']=='dismissed'
    owner.derive('run');assert owner.report('run')==updated

def test_C_stale_pagination(state):
    owner=setup(state);page=owner.report('run',limit=1);assert page['next_cursor']
    owner.command('run',Command(command_id='next',action='continue',expected_revision=1,budget=1));owner.derive('run')
    with pytest.raises(DomainError,match='stale_analysis_cursor'):owner.report('run',limit=1,after=page['next_cursor'])

def test_C_auth_workspace(state):
    owner=setup(state)
    app=create_app(replace(state[1],origins=('http://localhost:4100',)),store=state[0])
    with TestClient(app) as client:
        assert client.get('/v1/analysis/runs').status_code==401
        headers={'Authorization':'Bearer '+state[1].caller_token,'X-Tempo-Workspace':'other'}
        assert client.get('/v1/analysis/runs',headers=headers).status_code==403
        headers['X-Tempo-Workspace']='fixture'
        assert client.get('/v1/analysis/runs',headers=headers).status_code==200
        assert client.get('/v1/analysis/runs',headers={**headers,'Origin':'http://evil.invalid'}).status_code==403
        response=client.get('/v1/analysis/runs',headers={'Origin':'http://localhost:4100'})
        assert response.status_code==401 and response.headers['access-control-allow-origin']=='http://localhost:4100'

def test_C_proposal_not_activation(state):
    owner=setup(state);report=owner.report('run');item=report['items'][0]
    context=owner.export('run',item['finding_id'],report['generation'],'context')
    assert context['status']=='proposal_only' and not {'engine','response_proposal','actions'} & context['finding'].keys()
    with pytest.raises(DomainError,match='validated_A_proposal_unavailable'):owner.export('run',item['finding_id'],report['generation'],'response-proposal')
    assert owner.report('run')==report

def test_C_partial_scan_truth_native(state):
    owner=setup(state,budget=1);claim=owner.claim('run',builder);owner.accept(claim,result(claim));owner.derive('run')
    report=owner.report('run');assert report['unprocessed_count']>0 and not report['engine_scan_complete']
    assert owner.remaining('run')['items']

def test_input_immutable_and_partial_rejection(state):
    owner=setup(state);manifest=Manifest.model_validate_json((FIXTURES/'manifest.json').read_text())
    changed=manifest.model_copy(update={'producer_version':'changed'})
    with pytest.raises(DomainError,match='immutable_input_conflict'):owner.manifest(changed)
    with state[0].transaction(read_only=True) as db:
        for table in ('analysis_inputs','analysis_pages','analysis_results','analysis_reports','analysis_executions'):
            with pytest.raises(psycopg.Error):
                with db.transaction():db.execute('UPDATE '+table+" SET payload='{}'")

def test_native_workbench_producer_contract(state):
    owner=setup(state)
    report=owner.report('run')
    from tempo_intelligence.store import public
    app=create_app(state[1],store=state[0])
    with TestClient(app) as client:
        client.headers.update({'Authorization':'Bearer '+state[1].caller_token,'X-Tempo-Workspace':'fixture'})
        payload={'producer':'installed Intelligence with real disposable PostgreSQL','synthetic_source':True,
                 'bootstrap':client.get('/v1/analysis/bootstrap').json(),
                 'inputs':client.get('/v1/analysis/inputs').json(),'runs':client.get('/v1/analysis/runs').json(),'report':report,
                 'details':{item['finding_id']:owner.finding('run',item['finding_id'],report['generation']) for item in report['items']}}
        gap=next(item for item in report['items'] if item['category']=='practical_gap')
        command={'schema_version':1,'command_id':'browser-dismiss-gap','action':'dismiss','expected_revision':gap['finding_revision'],'budget':250}
        path='/v1/analysis/findings/'+gap['finding_id']+'/commands?run_id=run&generation='+str(report['generation'])
        response=client.post(path,json=command);assert response.status_code==200
        receipt=response.json();assert client.get('/v1/analysis/commands/'+command['command_id']).json()==receipt
        owner.derive('run');dismissed=owner.report('run')
        payload['dismissal']={'path':path,'command':command,'receipt':receipt,'report':dismissed,'runs':owner.runs(),'details':{item['finding_id']:owner.finding('run',item['finding_id'],dismissed['generation']) for item in dismissed['items']}}
    path=os.environ.get('TEMPO_C_BROWSER_FIXTURE_FILE')
    assert path, 'owning runner must provide task-owned producer output'
    Path(path).write_text(json.dumps(public(payload),indent=2)+'\n')

def test_source_correction_retains_report_and_fences_prior_execution(state):
    owner=setup(state);old_report=owner.report('run');old_claim=owner.claim('run',builder);remaining=owner.remaining('run',limit=1);assert remaining['next_after']
    manifest=json.loads((FIXTURES/'manifest.json').read_text());page=json.loads((FIXTURES/'games-0.json').read_text())
    page['revision']=2
    for game in page['games']:game['source_revision']=2
    page=Page.model_validate(page)
    manifest['revision']=2;manifest['pages'][0]['digest']=digest(page.model_dump(mode='json'))
    owner.manifest(Manifest.model_validate(manifest));owner.page(page);owner.finalize(manifest['input_id'],Command(command_id='finalize-2',action='finalize',expected_revision=2))
    owner.submit(RunRequest(schema_version=1,workspace_id='fixture',run_id='run',revision=2,expected_revision=1,input_id=manifest['input_id'],input_revision=2))
    with pytest.raises(DomainError,match='stale_remaining_cursor'):owner.remaining('run',after=remaining['next_after'])
    stale=owner.report('run');assert stale['status']=='stale' and stale['generation']==old_report['generation'] and stale['items']==old_report['items']
    with pytest.raises(DomainError,match='stale_analysis_execution'):owner.accept(old_claim,result(old_claim))
    assert owner.derive('run')
    current=owner.report('run');assert current['revision']==2 and all(f['representative']['source_revision']==2 for f in current['items'])


def test_dismissal_exact_replay_after_publication(state):
    owner=setup(state);report=owner.report('run');item=report['items'][0]
    command=Command(command_id='dismiss',action='dismiss',expected_revision=item['finding_revision'])
    response=owner.dismiss('run',item['finding_id'],report['generation'],command);owner.derive('run')
    assert owner.dismiss('run',item['finding_id'],report['generation'],command)==response


def test_engine_job_builder_cannot_change_captured_root(state):
    owner=setup(state)
    def wrong(*args):
        job=builder(*args);job['moves']=[] if job['moves'] else ['e2e4'];return job
    with pytest.raises(DomainError,match='analysis_job_input_mismatch'):owner.claim('run',wrong)
    assert owner.status('run')['progress'].get('running',0)==0

def test_three_additional_games_reopen_durably_across_correction(state):
    owner=setup(state);first=owner.report('run');gap=next(f for f in first['items'] if f['category']=='practical_gap')
    owner.dismiss('run',gap['finding_id'],first['generation'],Command(command_id='dismiss-gap',action='dismiss',expected_revision=gap['finding_revision']));owner.derive('run')
    manifest=json.loads((FIXTURES/'manifest.json').read_text());page=json.loads((FIXTURES/'games-0.json').read_text());manifest['revision']=2;page['revision']=2
    for index in range(3):
        game=deepcopy(page['games'][1]);game['game_id']='extra-'+str(index);page['games'].append(game)
    page=Page.model_validate(page);manifest['game_ids']=[g.game_id for g in page.games];manifest['pages'][0].update(game_count=len(page.games),digest=digest(page.model_dump(mode='json')))
    owner.manifest(Manifest.model_validate(manifest));owner.page(page);owner.finalize(manifest['input_id'],Command(command_id='finalize-reopen',action='finalize',expected_revision=2))
    owner.submit(RunRequest(schema_version=1,workspace_id='fixture',run_id='run',revision=2,expected_revision=1,input_id=manifest['input_id'],input_revision=2,position_budget=0));owner.derive('run')
    reopened=next(f for f in owner.report('run')['items'] if f['finding_id']==gap['finding_id']);assert reopened['supporting_game_count']==4 and reopened['dismissal_state']=='open'
    with state[0].transaction(read_only=True) as db:assert db.execute('SELECT state FROM analysis_dismissals WHERE finding_id=%s',(gap['finding_id'],)).fetchone()['state']=='open'

def test_analysis_shutdown_receipt_for_optional_off_assembly(state):
    import httpx
    from .test_server_postgres import installed_server,lifecycle_environment,wait_for_health
    owner=setup(state);environment,directory,secrets=lifecycle_environment(state)
    environment['TEMPO_INTELLIGENCE_WORKER_ENABLED']='0'
    with installed_server(environment,directory,secrets) as (process,origin):
        with httpx.Client(base_url=origin,headers={'Authorization':'Bearer '+state[1].caller_token}) as client:
            wait_for_health(client,process)
            assert client.get('/v1/analysis/runs/run/report').status_code==200
    assert process.poll() is not None
    path=Path(os.environ['TEMPO_C_BROWSER_FIXTURE_FILE']).parent/'optional-stop.json'
    path.write_text(json.dumps({'component':'Intelligence','installed':True,'report_read':True,'server_stopped':True,'returncode':process.returncode})+'\n')


def test_retry_insufficient_quality_preserves_results_and_advances_execution(state):
    owner=setup(state);claim=owner.claim('run',builder);shallow=result(claim);shallow['variations'][0]['depth']=12
    owner.accept(claim,shallow);owner.derive('run');assert owner.report('run')['insufficient_quality_count']==1
    assert any(item['error_code']=='insufficient_quality' for item in owner.remaining('run')['items'])
    owner.command('run',Command(command_id='retry-quality',action='retry',expected_revision=1))
    retried=owner.claim('run',builder);assert retried['position_id']==claim['position_id'] and retried['execution_generation']>claim['execution_generation']
    with state[0].transaction(read_only=True) as db:assert db.execute('SELECT count(*) FROM analysis_results').fetchone()['count']==1


def test_engine_opportunity_without_repertoire_color_mapping(state):
    import chess
    owner=AnalysisStore(state[0])
    raw=json.loads((FIXTURES/'games-0.json').read_text());game=raw['games'][0];game['learner_color']='black';game['contexts']=[]
    page=Page.model_validate({**raw,'input_id':'unmapped-engine','games':[game]})
    manifest=json.loads((FIXTURES/'manifest.json').read_text());manifest.update(input_id=page.input_id,game_ids=[game['game_id']],pages=[{'page_id':page.page_id,'game_count':1,'digest':digest(page.model_dump(mode='json'))}])
    owner.manifest(Manifest.model_validate(manifest));owner.page(page);owner.finalize(page.input_id,Command(command_id='finalize-unmapped',action='finalize',expected_revision=1))
    owner.submit(RunRequest(schema_version=1,workspace_id='fixture',run_id='unmapped',revision=1,input_id=page.input_id,input_revision=1,position_budget=1))
    root=owner.claim('unmapped',builder);wire=result(root);board=chess.Board(root['payload']['fen']);alternative=next(move.uci() for move in board.legal_moves if move.uci()!=root['payload']['actual_move'])
    wire['variations'][0].update(moves=[alternative],score={'kind':'centipawns','value':200});owner.accept(root,wire)
    played=owner.claim('unmapped',builder);owner.accept(played,result(played));owner.derive('unmapped')
    finding=next(item for item in owner.report('unmapped')['items'] if item['category']=='engine_opportunity')
    assert finding['learner_color']=='black' and finding['repertoire_id'] is None and finding['policy_id'] is None


def test_first_report_cannot_publish_over_a_sibling_run_dismissal(state):
    owner=setup(state);report=owner.report('run');item=report['items'][0]
    source=owner.status('run')['scope']
    owner.submit(RunRequest(schema_version=1,workspace_id='fixture',run_id='sibling',revision=1,input_id=source['input_id'],input_revision=source['input_revision'],position_budget=0))
    def dismiss():owner.dismiss('run',item['finding_id'],report['generation'],Command(command_id='sibling-dismiss',action='dismiss',expected_revision=item['finding_revision']))
    assert not owner.derive('sibling',before_publish=dismiss)
    assert owner.derive('sibling')
    assert next(f for f in owner.report('sibling')['items'] if f['finding_id']==item['finding_id'])['dismissal_state']=='dismissed'
