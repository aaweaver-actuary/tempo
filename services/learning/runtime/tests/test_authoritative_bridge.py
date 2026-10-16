"""Installed Content -> operator HTTP -> installed Game Evidence -> Learning.

Synthetic authored content is labeled. Every activation/receipt below comes from
the actual restricted Learning runtime; no acknowledgement is manufactured.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import resource
from threading import Thread
import uuid

import chess
import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo, conninfo_to_dict
import pytest

from test_game_evidence_runtime import producer
from test_restart import server
from test_runtime import head, review_payload, submit


def source_document():
    board=chess.Board()
    board.push_uci('e2e4')
    board.push_uci('e7e5')
    return {'schema_version':1,'workspace_id':'workspace-a','source_key':'a-synthetic-opening','title':'Complete synthetic authored opening',
        'repertoires':[{'key':'white','title':'White','learner_color':'white'}],
        'policies':[{'key':'main','repertoire_key':'white'}],
        'routes':[{'key':'e4','starting_fen':chess.STARTING_FEN,'moves':['e2e4','e7e5','g1f3']},
            {'key':'d4','starting_fen':chess.STARTING_FEN,'moves':['d2d4']}],
        'memberships':[{'route_key':key,'policy_key':'main','repertoire_key':'white'} for key in ('e4','d4')],
        'exercises':[{'key':'root','decision_key':'root','policy_key':'main','starting_fen':chess.STARTING_FEN,'prompt':'Play e4 or d4',
            'specification':{'type':'move_line','mode':'single','grading_policy':'reference','accepted_lines':[['e2e4'],['d2d4']]}},
            {'key':'knight','decision_key':'knight','policy_key':'main','starting_fen':board.fen(),'prompt':'Develop the knight',
             'specification':{'type':'move_line','mode':'single','grading_policy':'reference','accepted_lines':[['g1f3']]}}],
        'bindings':[{'route_key':'e4','policy_key':'main','ply':0,'exercise_key':'root'},
            {'route_key':'d4','policy_key':'main','ply':0,'exercise_key':'root'},
            {'route_key':'e4','policy_key':'main','ply':2,'exercise_key':'knight'}]}


def run_cli(executable,arguments,environment,directory,success=True,timeout=120):
    process=subprocess.run([str(executable),*map(str,arguments)],env=environment,cwd=directory,capture_output=True,text=True,timeout=timeout)
    if success:
        assert process.returncode==0,(process.stdout+process.stderr)[-3000:]
    return json.loads(process.stdout) if process.returncode==0 else process


@pytest.fixture
def authored(case,tmp_path):
    cli=Path(os.environ['TEMPO_LEARNING_TEST_CONTENT_CLI'])
    bridge_cli=Path(os.environ['TEMPO_LEARNING_TEST_BRIDGE_CLI'])
    admin=make_conninfo(case.admin,dbname='postgres')
    name='content_'+uuid.uuid4().hex[:12]
    owner,runtime=name+'_owner',name+'_runtime'
    owner_password,runtime_password=uuid.uuid4().hex,uuid.uuid4().hex
    with psycopg.connect(admin,autocommit=True) as database:
        for role,password in ((owner,owner_password),(runtime,runtime_password)):
            database.execute(sql.SQL('CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}').format(sql.Identifier(role),sql.Literal(password)))
        database.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(name),sql.Identifier(owner)))
        database.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(name)))
        database.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(name),sql.Identifier(runtime)))
    environment={key:value for key,value in os.environ.items() if not key.startswith(('TEMPO_','PG','PYTHON','CELERY_'))}
    environment.update(TEMPO_CONTENT_DATABASE_URL=make_conninfo(admin,dbname=name,user=runtime,password=runtime_password),
        TEMPO_CONTENT_MIGRATION_DATABASE_URL=make_conninfo(admin,dbname=name,user=owner,password=owner_password),
        TEMPO_CONTENT_WORKSPACE='workspace-a',TEMPO_CONTENT_RUNTIME_ROLE=runtime)
    try:
        run_cli(cli,['migrate'],environment,tmp_path)
        environment.pop('TEMPO_CONTENT_MIGRATION_DATABASE_URL')
        yield cli,bridge_cli,environment
    finally:
        with psycopg.connect(admin,autocommit=True) as database:
            database.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
            for role in (runtime,owner):
                database.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


@contextmanager
def drop_first_activation(origin):
    dropped=[]
    class Forward(BaseHTTPRequestHandler):
        def forward(self):
            raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
            response=httpx.request(self.command,origin+self.path,content=raw or None,headers={'Authorization':self.headers['Authorization'],'Content-Type':'application/json'},timeout=15)
            if self.path=='/internal/publications/v2/activate' and not dropped:
                assert response.status_code==200,response.text
                dropped.append(response.json())
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.send_response(response.status_code)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)
        do_GET=forward
        do_POST=forward
        def log_message(self,*arguments):
            pass
    proxy=ThreadingHTTPServer(('127.0.0.1',0),Forward)
    thread=Thread(target=proxy.serve_forever,daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:'+str(proxy.server_port),dropped
    finally:
        proxy.shutdown()
        proxy.server_close()
        thread.join(timeout=3)


def manifest_for(source,path):
    return {'schema_version':1,'kind':'authored_import','workspace_id':'workspace-a','source_key':source['source_key'],'title':source['title'],'complete':True,
        'provenance':{'kind':'synthetic','source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'description':'A installed authoritative bridge rehearsal; not Andy repertoire data'}}


def outstanding(case,card_id):
    from tempo_learning_remediation import PostgresRepository
    with psycopg.connect(case.configuration.database_url,options='-c search_path=learning,pg_catalog',connect_timeout=2) as database:
        database.execute('SET TRANSACTION READ ONLY')
        return PostgresRepository(database,case.configuration.workspace_id).has_outstanding(card_id)


def inspect_rows(case,statement,parameters=()):
    # Fixture assertions use the restricted runtime role, without spending a
    # foreground acquisition budget merely to inspect committed postconditions.
    with psycopg.connect(case.configuration.database_url,connect_timeout=2) as database:
        database.execute('SET TRANSACTION READ ONLY')
        database.execute("SET LOCAL statement_timeout='5s'")
        return database.execute(statement,parameters).fetchall()


def served_head(client):
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        response=client.get('/api/queue/window?include_opening_evidence=true')
        assert response.status_code in (200,503),response.text
        if response.status_code==200 and response.json()['cards']:
            return response.json()['cards'][0]
        time.sleep(.05)
    raise AssertionError('Installed Learning did not issue the actual Study head')


def save_review(client,card):
    operation_id=uuid.uuid4().hex
    response=client.post('/api/cards/'+card['id']+'/review',json=review_payload(card),headers={'Idempotency-Key':operation_id})
    assert response.status_code==200,response.text
    assert client.get('/api/operations/'+operation_id).json()['response']==response.json()
    return response


def test_A_actual_evidence_delivery_export_proposal_and_optional_off_study(case,producer,authored,tmp_path):
    content_cli,bridge_cli,environment=authored
    source=source_document()
    source_path=tmp_path/'source.json'
    source_path.write_text(json.dumps(source))
    manifest_path=tmp_path/'source-manifest.json'
    manifest_path.write_text(json.dumps(manifest_for(source,source_path)))
    draft=run_cli(content_cli,['import','plan',source_path,'--manifest',manifest_path,'--expected-revision','0'],environment,tmp_path)
    assert inspect_rows(case,'SELECT count(*) FROM learning.cards')[0][0]==0
    approved=run_cli(content_cli,['draft','approve',draft['id'],'--digest',draft['digest']],environment,tmp_path)
    token=tmp_path/'operator-token'
    token.write_text(case.configuration.operator_token)
    token.chmod(0o600)
    with server(case,tmp_path) as (client,process),drop_first_activation(str(client.base_url).rstrip('/')) as (origin,dropped):
        arguments=['--content-cli',content_cli,'--learning-origin',origin,'--operator-token-file',token]
        for _ in range(5):
            failed=run_cli(bridge_cli,[*arguments,'apply',approved['id']],environment,tmp_path,success=False)
            if dropped:
                break
            assert isinstance(failed,subprocess.CompletedProcess),(failed,dropped)
            time.sleep(.1)
        assert isinstance(failed,subprocess.CompletedProcess),failed
        assert failed.returncode!=0 and len(dropped)==1,(failed.stdout,failed.stderr)
        process.restart_service()
        completed=run_cli(bridge_cli,[*arguments,'reconcile',approved['id']],environment,tmp_path)
        assert completed['state']=='completed'
        assert completed['document']['receipt']['acknowledgement']==dropped[0]
        assert inspect_rows(case,'SELECT count(*) FROM learning.cards')[0][0]==2
        snapshot_path=tmp_path/'actual-snapshot.json'
        snapshot=run_cli(bridge_cli,[*arguments,'snapshot',approved['id'],'--output',snapshot_path],environment,tmp_path)
        assert snapshot['activation']==dropped[0] and snapshot['content_export']['provenance']['kind']=='synthetic'
        producer.run('load-snapshot',snapshot_path)
        issued=served_head(client)
        game_ids=[]
        for key,moves in (('miss','1. c4'),('gap','1. e4 c5 2. Nf3'),('alternative','1. d4'),('covered','1. e4 e5 2. Nf3')):
            pgn=tmp_path/(key+'.pgn')
            pgn.write_text('[Event "A synthetic '+key+'"]\n[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n'+moves+' 1-0\n')
            game_ids.append(producer.run('import-pgn',pgn,'--workspace','workspace-a','--player','Learner','--source-id',key)['game_id'])
        learning_endpoint=str(client.base_url).rstrip('/')+'/internal/evidence'
        producer.run('work','--max-jobs','100','--learning-endpoint',learning_endpoint)
        assert outstanding(case,issued['id'])
        assert producer.run('report',game_ids[0],'--workspace','workspace-a')['delivery']['status']=='completed'
        assert producer.run('report',game_ids[2],'--workspace','workspace-a')['findings'][0]['status']=='matched'
        old=save_review(client,issued)
        assert old.status_code==200,old.text
        assert outstanding(case,issued['id']), 'Already-issued attempt must not settle later evidence'
        fresh=served_head(client)
        assert fresh['id']==issued['id']
        review=save_review(client,fresh)
        assert review.status_code==200,review.text
        assert not outstanding(case,issued['id'])
        assert client.get('/api/queue/window?include_opening_evidence=true').status_code==200
        before=inspect_rows(case,'SELECT count(*) FROM learning.remediation_obligation_heads')[0][0]
        producer.run('work','--max-jobs','100','--learning-endpoint',learning_endpoint)
        assert inspect_rows(case,'SELECT count(*) FROM learning.remediation_obligation_heads')[0][0]==before==1
        output=tmp_path/'analysis-inputs'
        exported=producer.run('analysis-export','create','--workspace','workspace-a','--output',output)
        repeated=producer.run('analysis-export','create','--workspace','workspace-a','--output',output)
        assert repeated==exported
        assert producer.run('analysis-export','verify',output)['status']=='verified'
        evidence=Path(os.environ['TEMPO_LEARNING_TEST_BRIDGE_EVIDENCE'])
        evidence.mkdir(parents=True,exist_ok=True)
        shutil.copytree(output,evidence/'analysis-inputs',dirs_exist_ok=True)
        for path in (source_path,manifest_path,snapshot_path):
            shutil.copy2(path,evidence/path.name)
        (evidence/'activation.json').write_text(json.dumps(dropped[0],indent=2))
        deviation_pages=[json.loads((output/item['filename']).read_text()) for item in exported['pages'] if item['kind']=='deviations']
        deviation=next(item for page in deviation_pages for item in page['items'] if item['game_id']==game_ids[1])
        assert deviation['opponent_move']=='c7c5' and deviation['status']=='established_opponent_deviation'
        assert deviation['response_inventory']['complete'] and deviation['response_inventory']['selected_reply_states']==['uncovered']
        position_pages=[json.loads((output/item['filename']).read_text()) for item in exported['pages'] if item['kind']=='positions']
        observed=[item['opponent_decision'] for page in position_pages for item in page['items'] if item.get('opponent_decision') and item['ply']==1]
        assert len(observed)==2 and {item['is_deviation'] for item in observed}=={False,True}
        inventory=deviation['legal_reply_inventory']
        assert inventory['complete'] and inventory['scopes'] and len(inventory['moves'])==20
        proposal={'schema_version':1,'kind':'response_proposal','status':'draft','proposal_id':'actual-gap-proposal',
            'workspace_id':'workspace-a','source_key':source['source_key'],'repertoire_key':'white','policy_key':'main','learner_color':'white',
            'expected_source_revision':snapshot['publication']['source_revision'],'source_digest':snapshot['content_export']['source']['source_digest'],
            'route_key':'e4','anchor_ply':1,'opponent_move':deviation['opponent_move'],'decision_fen':deviation['decision_fen'],
            'accepted_lines':[['g1f3']],'provenance':{'kind':'synthetic','evidence_refs':[game_ids[1],exported['input_id']]}}
        proposal_path=tmp_path/'response-proposal.json'
        proposal_path.write_text(json.dumps(proposal))
        proposed=run_cli(content_cli,['draft','create','--proposal',proposal_path],environment,tmp_path)
        assert proposed['status']=='draft' and inspect_rows(case,'SELECT count(*) FROM learning.cards')[0][0]==2
        assert run_cli(content_cli,['draft','create','--proposal',proposal_path],environment,tmp_path)['id']==proposed['id']
        updated=run_cli(content_cli,['draft','approve',proposed['id'],'--digest',proposed['digest']],environment,tmp_path)
        applied=run_cli(bridge_cli,[*arguments,'apply',updated['id']],environment,tmp_path)
        assert applied['state']=='completed' and inspect_rows(case,'SELECT count(*) FROM learning.cards')[0][0]==3
        # Historical receipt remains real, but is no longer current serving authority.
        historical_path=tmp_path/'historical-snapshot.json'
        historical=run_cli(bridge_cli,[*arguments,'snapshot',approved['id'],'--output',historical_path],environment,tmp_path)
        assert historical['activation']['activation_revision']==1 and historical['current_serving']['activation_revision']==2
        successor_path=tmp_path/'successor-snapshot.json'
        successor=run_cli(bridge_cli,[*arguments,'snapshot',updated['id'],'--output',successor_path],environment,tmp_path)
        producer.run('load-snapshot',successor_path)
        stale=run_cli(producer.python,['-I','-m','tempo_game_evidence.cli','load-snapshot',historical_path],producer.environment,tmp_path,success=False)
        assert isinstance(stale,subprocess.CompletedProcess) and stale.returncode!=0
        producer.run('work','--max-jobs','100','--learning-endpoint',learning_endpoint)
        assert not outstanding(case,issued['id']), 'Metadata generation replay cannot re-promote settled observations'
        correction=tmp_path/'corrected-miss.pgn'
        correction.write_text('[Event "A source correction"]\n[White "Learner"]\n[Black "Opponent"]\n[Result "1-0"]\n\n1. e4 1-0\n')
        assert producer.run('import-pgn',correction,'--workspace','workspace-a','--player','Learner','--source-id','miss')['game_id']==game_ids[0]
        producer.run('work','--max-jobs','100','--learning-endpoint',learning_endpoint)
        assert producer.run('report',game_ids[0],'--workspace','workspace-a')['findings'][0]['status']=='matched'
        empty=deepcopy(source)
        for collection in ('routes','memberships','exercises','bindings'):
            empty[collection]=[]
        retirement_source=tmp_path/'retirement-source.json'
        retirement_source.write_text(json.dumps(empty))
        retirement_manifest=tmp_path/'retirement-manifest.json'
        retirement_manifest.write_text(json.dumps(manifest_for(empty,retirement_source)))
        retirement_draft=run_cli(content_cli,['import','plan',retirement_source,'--manifest',retirement_manifest,'--expected-revision','2'],environment,tmp_path)
        retirement_operation=run_cli(content_cli,['draft','approve',retirement_draft['id'],'--digest',retirement_draft['digest']],environment,tmp_path)
        retired=run_cli(bridge_cli,[*arguments,'apply',retirement_operation['id']],environment,tmp_path)
        assert retired['state']=='completed' and retired['document']['receipt']['acknowledgement']['status']=='retired'
        assert inspect_rows(case,'SELECT count(*) FROM learning.cards WHERE serving_active=1')[0][0]==0
        # Prove all three runtime roles are denied foreign domain databases.
        learning_name=conninfo_to_dict(case.admin)['dbname']
        content_name=conninfo_to_dict(environment['TEMPO_CONTENT_DATABASE_URL'])['dbname']
        evidence_name=conninfo_to_dict(producer.environment['TEMPO_GAME_EVIDENCE_DATABASE_URL'])['dbname']
        for dsn,names in ((environment['TEMPO_CONTENT_DATABASE_URL'],(learning_name,evidence_name)),(case.configuration.database_url,(content_name,evidence_name)),(producer.environment['TEMPO_GAME_EVIDENCE_DATABASE_URL'],(learning_name,content_name))):
            for name in names:
                with pytest.raises(psycopg.OperationalError,match='permission denied'):
                    psycopg.connect(make_conninfo(dsn,dbname=name),connect_timeout=2)
        evidence=Path(os.environ['TEMPO_LEARNING_TEST_BRIDGE_EVIDENCE'])
        evidence.mkdir(parents=True,exist_ok=True)
        shutil.copytree(output,evidence/'analysis-inputs',dirs_exist_ok=True)
        for path in (source_path,manifest_path,snapshot_path,proposal_path,historical_path,successor_path):
            shutil.copy2(path,evidence/path.name)
        (evidence/'activation.json').write_text(json.dumps(dropped[0],indent=2))
        (evidence/'retirement.json').write_text(json.dumps(retired['document']['receipt']['acknowledgement'],indent=2))


@pytest.mark.parametrize('count',[129,512,2048])
def test_A_complete_large_authored_source_traverses_installed_bridge(case,authored,tmp_path,count):
    from test_content_bridge import content_bundle
    content_cli,bridge_cli,environment=authored
    sample=content_bundle(count)
    source=source_document()
    source['routes']=[]
    source['memberships']=[]
    source['exercises']=[]
    source['bindings']=[]
    for index,definition in enumerate(sample['publication']['definitions']):
        key='decision-'+str(index)
        source['routes'].append({'key':key,'starting_fen':definition['starting_fen'],'moves':definition['specification']['accepted_lines'][0]})
        source['memberships'].append({'route_key':key,'policy_key':'main','repertoire_key':'white'})
        source['exercises'].append({'key':key,'decision_key':key,'policy_key':'main','starting_fen':definition['starting_fen'],'prompt':definition['prompt'],'specification':definition['specification']})
        source['bindings'].append({'route_key':key,'policy_key':'main','ply':0,'exercise_key':key})
    source_path=tmp_path/'complete-source.json'
    source_path.write_text(json.dumps(source))
    manifest_path=tmp_path/'manifest.json'
    manifest_path.write_text(json.dumps(manifest_for(source,source_path)))
    started=time.perf_counter()
    draft=run_cli(content_cli,['import','plan',source_path,'--manifest',manifest_path,'--expected-revision','0'],environment,tmp_path)
    approved=run_cli(content_cli,['draft','approve',draft['id'],'--digest',draft['digest']],environment,tmp_path)
    planning=time.perf_counter()-started
    token=tmp_path/'large-operator-token'
    token.write_text(case.configuration.operator_token)
    token.chmod(0o600)
    with server(case,tmp_path) as (client,process):
        arguments=['--content-cli',content_cli,'--learning-origin',str(client.base_url).rstrip('/'),'--operator-token-file',token]
        started=time.perf_counter()
        completed=run_cli(bridge_cli,[*arguments,'apply',approved['id'],'--max-slices','10000'],environment,tmp_path,timeout=300)
        applying=time.perf_counter()-started
        assert completed['state']=='completed',completed
        publication=completed['document']['export']['publication']
        assert len(publication['definitions'])==count
        assert inspect_rows(case,'SELECT count(*) FROM learning.cards WHERE serving_active=1')[0][0]==count
        replay=run_cli(bridge_cli,[*arguments,'reconcile',approved['id']],environment,tmp_path)
        assert replay==completed
        evidence=Path(os.environ['TEMPO_LEARNING_TEST_BRIDGE_EVIDENCE'])
        evidence.mkdir(parents=True,exist_ok=True)
        (evidence/f'installed-capacity-{count}.json').write_text(json.dumps({'definitions':count,'source_bytes':source_path.stat().st_size,
            'publication_bytes':len(json.dumps(publication).encode()),'route_count':len(source['routes']),'route_plies':1,
            'plan_approval_seconds':planning,'validation_publication_delivery_seconds':applying,
            'maximum_child_rss':resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,'publication_id':publication['publication_id'],
            'publication_generation':publication['publication_generation'],'activation':completed['document']['receipt']['acknowledgement']},indent=2))
