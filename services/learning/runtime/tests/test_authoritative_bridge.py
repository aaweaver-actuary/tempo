"""Installed Content -> operator HTTP -> installed Game Evidence -> Learning.

Synthetic authored content is labeled. Every activation/receipt below comes from
the actual restricted Learning runtime; no acknowledgement is manufactured.
"""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
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
import signal
import sys
from threading import Barrier, Lock, Thread
import uuid

import chess
import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo, conninfo_to_dict
import pytest

from test_game_evidence_runtime import producer, installed_producer
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
    command = [str(executable), *map(str, arguments)]
    # The operator spawns Content. A timed-out parent must not leave that child
    # holding the disposable database and defeating the owning fixture cleanup.
    child = subprocess.Popen(command, env=environment, cwd=directory,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        output, errors = child.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            child.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.communicate(timeout=3)
        raise
    process = subprocess.CompletedProcess(command, child.returncode, output, errors)
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
def drop_first_activation(origin, snapshot_barrier=None):
    dropped=[]
    snapshot_tickets=[0,1] if snapshot_barrier else []
    snapshot_lock=Lock()
    class Forward(BaseHTTPRequestHandler):
        def forward(self):
            raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
            response=httpx.request(self.command,origin+self.path,content=raw or None,headers={'Authorization':self.headers['Authorization'],'Content-Type':'application/json'},timeout=15)
            if '/status?generation=' in self.path:
                with snapshot_lock:
                    synchronize=bool(snapshot_tickets)
                    if synchronize:
                        snapshot_tickets.pop()
                if synchronize:
                    # Both real observations were read before either operator
                    # may checkpoint them into actual Content PostgreSQL.
                    snapshot_barrier.wait(timeout=10)
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
    with server(case,tmp_path) as (client,process),drop_first_activation(str(client.base_url).rstrip('/'),Barrier(2)) as (origin,dropped):
        arguments=['--content-cli',content_cli,'--learning-origin',origin,'--operator-token-file',token]
        for _ in range(5):
            failed=run_cli(bridge_cli,[*arguments,'apply',approved['id']],environment,tmp_path,success=False)
            if dropped:
                break
            assert isinstance(failed,subprocess.CompletedProcess),(failed,dropped)
            time.sleep(.1)
        assert isinstance(failed,subprocess.CompletedProcess),failed
        assert failed.returncode!=0 and len(dropped)==1,(failed.stdout,failed.stderr)
        uncertain=run_cli(content_cli,['operation','show',approved['id']],environment,tmp_path)
        assert uncertain['state']=='applying' and uncertain['document']['delivery']
        process.restart_service()
        completed=run_cli(bridge_cli,[*arguments,'reconcile',approved['id']],environment,tmp_path)
        assert completed['state']=='completed'
        assert completed['document']['receipt']['acknowledgement']==dropped[0]
        assert inspect_rows(case,'SELECT count(*) FROM learning.cards')[0][0]==2
        snapshot_path=tmp_path/'actual-snapshot.json'
        duplicate_snapshot_path=tmp_path/'duplicate-snapshot.json'
        def concurrent_snapshot(path):
            return run_cli(bridge_cli,[*arguments,'snapshot',approved['id'],'--output',path],environment,tmp_path)
        with ThreadPoolExecutor(max_workers=2) as snapshots:
            first_snapshot,second_snapshot=list(snapshots.map(concurrent_snapshot,[snapshot_path,duplicate_snapshot_path]))
        assert first_snapshot==second_snapshot
        snapshot=first_snapshot
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
        comparison_pages=[json.loads((output/item['filename']).read_text()) for item in exported['pages'] if item['kind']=='comparisons']
        delivered=next(item for page in comparison_pages for item in page['items'] if item['game']['game_id']==game_ids[0])['delivery']
        assert delivered['status']=='completed' and delivered['inspection_source']=='game_evidence_outbox'
        assert delivered['batches'] and all(item['acknowledgement']['status']=='completed' for item in delivered['batches'])
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
        assert historical['snapshot_id'] != successor['snapshot_id']
        fresh_path = tmp_path / 'historical-first-producer'
        fresh_path.mkdir()
        with installed_producer(case, fresh_path) as fresh:
            fresh.run('load-snapshot', historical_path)
            fresh.run('load-snapshot', successor_path)
            assert fresh.query("SELECT document->'publication'->>'publication_generation' FROM snapshots WHERE digest=(SELECT snapshot_digest FROM workspace_inputs WHERE workspace_id='workspace-a')")[0][0] == '2'
        producer.run('load-snapshot',successor_path)
        current_digest = producer.query("SELECT snapshot_digest FROM workspace_inputs WHERE workspace_id='workspace-a'")[0][0]
        stale=run_cli(producer.python,['-I','-m','tempo_game_evidence.cli','load-snapshot',historical_path],producer.environment,tmp_path,success=False)
        assert isinstance(stale,subprocess.CompletedProcess) and stale.returncode!=0
        assert producer.query("SELECT snapshot_digest FROM workspace_inputs WHERE workspace_id='workspace-a'")[0][0] == current_digest
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
        retired_snapshot_path=tmp_path/'post-retirement-snapshot.json'
        retired_snapshot=run_cli(bridge_cli,[*arguments,'snapshot',approved['id'],'--output',retired_snapshot_path],environment,tmp_path)
        assert retired_snapshot['current_serving']['status']=='retired' and retired_snapshot['revision']>historical['revision']
        retired_current_path=tmp_path/'post-retirement-current-snapshot.json'
        retired_current=run_cli(bridge_cli,[*arguments,'snapshot',updated['id'],'--output',retired_current_path],environment,tmp_path)
        assert retired_current['publication']['publication_generation']==2 and retired_current['current_serving']['status']=='retired'
        deliveries_before=producer.query('SELECT delivery_id,status,acknowledgement FROM outbox ORDER BY delivery_id')
        producer.run('load-snapshot',retired_current_path)
        producer.run('work','--max-jobs','100','--learning-endpoint',learning_endpoint)
        assert producer.run('report',game_ids[0],'--workspace','workspace-a')['readiness']=='unknown'
        assert producer.query('SELECT delivery_id,status,acknowledgement FROM outbox ORDER BY delivery_id')==deliveries_before
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
        for path in (source_path,manifest_path,snapshot_path,proposal_path,historical_path,successor_path,retired_snapshot_path,retired_current_path):
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
        invocations = 0
        # Exercise durable bounded resume with the same original 10,000-slice
        # allowance. Each invocation retains the unchanged 300-second limit.
        for consumed in range(0, 10000, 64):
            invocations += 1
            completed=run_cli(bridge_cli,[*arguments,'apply',approved['id'],'--max-slices',min(64,10000-consumed)],environment,tmp_path,timeout=300)
            if completed['state']=='completed':
                break
            assert completed['state']=='pending_validation' and completed['operation_id']==approved['id']
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
            'plan_approval_seconds':planning,'validation_publication_delivery_seconds':applying,'operator_invocations':invocations,'maximum_slices_per_invocation':64,'total_slice_allowance':10000,
            'maximum_child_rss':resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,'publication_id':publication['publication_id'],
            'publication_generation':publication['publication_generation'],'activation':completed['document']['receipt']['acknowledgement']},indent=2))


def test_A_corrected_source_activates_after_original_terminal_failure(case, authored, tmp_path):
    content_cli, bridge_cli, environment = authored
    token = tmp_path / 'operator-token'
    token.write_text(case.configuration.operator_token)
    token.chmod(0o600)
    source = source_document()
    def approve_source(document, revision, aliases=None):
        source_path = tmp_path / ('source-' + str(revision) + '.json')
        source_path.write_text(json.dumps(document))
        manifest = manifest_for(document, source_path)
        manifest['legacy_aliases'] = aliases or {}
        manifest_path = tmp_path / ('manifest-' + str(revision) + '.json')
        manifest_path.write_text(json.dumps(manifest))
        draft = run_cli(content_cli, ['import', 'plan', source_path, '--manifest', manifest_path, '--expected-revision', revision], environment, tmp_path)
        return run_cli(content_cli, ['draft', 'approve', draft['id'], '--digest', draft['digest']], environment, tmp_path)
    original = approve_source(source, 0)
    with server(case, tmp_path) as (client, process):
        origin = str(client.base_url).rstrip('/')
        arguments = ['--content-cli', content_cli, '--learning-origin', origin, '--operator-token-file', token]
        assert run_cli(bridge_cli, [*arguments, 'apply', original['id']], environment, tmp_path)['state'] == 'completed'
        previous = client.get('/internal/publications/' + run_cli(content_cli, ['source', 'show', source['source_key']], environment, tmp_path)['publication_id'] + '/status', headers={'Authorization': 'Bearer ' + case.configuration.operator_token}).json()
        board = chess.Board()
        board.push_uci('e2e4'); board.push_uci('c7c5')
        source['routes'].append({'key': 'sicilian', 'starting_fen': chess.STARTING_FEN, 'moves': ['e2e4','c7c5','g1f3']})
        source['memberships'].append({'route_key': 'sicilian', 'policy_key': 'main', 'repertoire_key': 'white'})
        source['exercises'].append({'key': 'c5-response', 'decision_key': 'c5-response', 'policy_key': 'main', 'starting_fen': board.fen(en_passant='fen'), 'prompt': 'Respond to c5',
            'specification': {'type':'move_line','mode':'single','grading_policy':'reference','accepted_lines':[['g1f3']]}})
        source['bindings'].extend([{'route_key': 'sicilian','policy_key':'main','ply':0,'exercise_key':'root'}, {'route_key':'sicilian','policy_key':'main','ply':2,'exercise_key':'c5-response'}])
        rejected = approve_source(source, 1, {'c5-response': 'missing-historical-card'})
        result = run_cli(bridge_cli, [*arguments, 'apply', rejected['id']], environment, tmp_path, success=False)
        assert isinstance(result, subprocess.CompletedProcess) and result.returncode == 2
        failed = run_cli(content_cli, ['operation', 'show', rejected['id']], environment, tmp_path)
        assert failed['state'] == 'failed' and failed['document']['failure']['state'] == 'failed'
        remote_id = rejected['id'] + ':activate'
        headers = {'Authorization': 'Bearer ' + case.configuration.operator_token}
        receipt = client.get('/internal/publications/operations/' + remote_id, headers=headers).json()
        assert receipt['state'] == 'failed' and receipt['request'] == failed['document']['delivery']['request']
        assert inspect_rows(case, 'SELECT count(*) FROM learning.cards WHERE serving_active=1')[0][0] == 2
        status = client.get('/internal/publications/' + previous['publication_id'] + '/status', headers=headers).json()
        assert (status['publication_generation'], status['activation_revision']) == (previous['publication_generation'], previous['activation_revision'])
        process.restart_service()
        replay = run_cli(content_cli, ['operation','record-failure', rejected['id'], '--learning-origin', origin, '--operator-token-file', token], environment, tmp_path)
        assert replay['document']['failure'] == failed['document']['failure']
        current = run_cli(content_cli, ['source','show', source['source_key']], environment, tmp_path)
        assert current['requested_revision'] == 2
        source['title'] = 'Corrected complete synthetic source'
        corrected = approve_source(source, current['requested_revision'])
        assert corrected['id'] != rejected['id']
        completed = run_cli(bridge_cli, [*arguments,'apply',corrected['id']], environment, tmp_path)
        assert completed['state'] == 'completed'
        assert inspect_rows(case, 'SELECT count(*) FROM learning.cards WHERE serving_active=1')[0][0] == 3
        assert client.post('/internal/publications/operations/' + remote_id + '/retry', json={}, headers=headers).status_code == 409
        assert run_cli(content_cli, ['operation','record-failure', rejected['id'], '--learning-origin', origin, '--operator-token-file', token], environment, tmp_path)['document']['failure'] == failed['document']['failure']
        assert run_cli(content_cli, ['operation','show', rejected['id']], environment, tmp_path)['state'] == 'failed'
        evidence = Path(os.environ['TEMPO_LEARNING_TEST_BRIDGE_EVIDENCE'])
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence/'terminal-recovery.json').write_text(json.dumps({'failed_content': failed, 'learning_failure': receipt, 'corrected_content': completed}, indent=2, default=str))


def test_A_cli_timeout_terminates_owned_descendants(tmp_path):
    marker = tmp_path / 'orphan-finished'
    started = tmp_path / 'child-started'
    child_code = "import time; from pathlib import Path; Path(" + repr(str(started)) + ").write_text('started'); time.sleep(2); Path(" + repr(str(marker)) + ").write_text('unexpected surviving child')"
    parent_code = "import subprocess,sys; subprocess.run([sys.executable,'-c'," + repr(child_code) + "])"
    with pytest.raises(subprocess.TimeoutExpired):
        run_cli(sys.executable, ['-c', parent_code], os.environ.copy(), tmp_path, timeout=1)
    assert started.exists(), 'The descendant must have actually started before testing its termination'
    time.sleep(2.1)
    assert not marker.exists()


def approve_authored(content_cli, environment, directory, source, revision):
    source_path = directory / ('source-' + uuid.uuid4().hex + '.json')
    source_path.write_text(json.dumps(source))
    manifest_path = source_path.with_suffix('.manifest.json')
    manifest_path.write_text(json.dumps(manifest_for(source, source_path)))
    draft = run_cli(content_cli, ['import', 'plan', source_path, '--manifest', manifest_path,
        '--expected-revision', revision], environment, directory)
    return run_cli(content_cli, ['draft', 'approve', draft['id'], '--digest', draft['digest']], environment, directory)


@contextmanager
def publication_fault_proxy(origin, mode, controls=None, study_token=None):
    """Forward real requests; only lose responses or create a real stage conflict."""
    observed = []
    class Forward(BaseHTTPRequestHandler):
        def forward(self):
            current_mode = (controls or {}).get('mode', mode)
            started = time.perf_counter()
            raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            headers = {'Authorization': self.headers['Authorization'], 'Content-Type': 'application/json'}
            is_stage = self.path == '/internal/publications/v2/stage'
            is_abort = self.path.endswith('/abort')
            if is_abort and current_mode == 'blocked_abort':
                headers['Authorization'] = 'Bearer ' + study_token
            if is_stage and current_mode in ('stage_conflict', 'lost_abort', 'blocked_abort') and not observed:
                conflicting = json.loads(raw)
                conflicting['facts'][0]['due_date'] = '2001-01-01'
                accepted = httpx.post(origin + self.path, json=conflicting, headers=headers, timeout=15)
                assert accepted.status_code == 200, accepted.text
                observed.append({'prior_staging': accepted.json()})
            response = httpx.request(self.command, origin + self.path, content=raw or None, headers=headers, timeout=15)
            if controls is not None:
                controls.setdefault('requests', []).append({'method': self.command, 'path': self.path, 'status': response.status_code, 'seconds': time.perf_counter() - started})
            if self.path.endswith('/status') and current_mode == 'retire_after_status' and not observed:
                current = response.json()
                retirement = {'retirement_id': 'other-retirement-' + uuid.uuid4().hex,
                    'publication_id': current['publication_id'], 'publication_generation': current['publication_generation'],
                    'expected_activation_revision': current['activation_revision']}
                retired = httpx.post(origin + '/internal/publications/retire', json=retirement, headers=headers, timeout=15)
                assert retired.status_code == 200, retired.text
                observed.append({'concurrent_retirement': retired.json()})
            if (is_stage and current_mode == 'lost_stage' and not observed) or (is_abort and current_mode == 'lost_abort' and len(observed) == 1):
                assert response.status_code == 200, response.text
                observed.append({'lost_response': response.json()})
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.send_response(response.status_code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)
        do_GET = forward
        do_POST = forward
        def log_message(self, *arguments):
            pass
    proxy = ThreadingHTTPServer(('127.0.0.1', 0), Forward)
    thread = Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(proxy.server_port), observed
    finally:
        proxy.shutdown()
        proxy.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize('scenario', ['unsupported', 'empty', 'stage_conflict', 'lost_stage', 'lost_abort'])
def test_A_installed_pre_activation_recovery(case, authored, tmp_path, scenario):
    content_cli, bridge_cli, environment = authored
    token = tmp_path / 'operator-token'
    token.write_text(case.configuration.operator_token)
    token.chmod(0o600)
    valid = source_document()
    source = deepcopy(valid)
    if scenario == 'unsupported':
        source['exercises'][0]['specification'] = {'type': 'explanation', 'rubric': 'Control the center'}
    if scenario == 'empty':
        source.update(routes=[], memberships=[], exercises=[], bindings=[])
    original = approve_authored(content_cli, environment, tmp_path, source, 0)
    headers = {'Authorization': 'Bearer ' + case.configuration.operator_token}
    controls = {'mode': scenario}
    with server(case, tmp_path) as (client, process), publication_fault_proxy(str(client.base_url).rstrip('/'), scenario, controls) as (origin, observed):
        arguments = ['--content-cli', content_cli, '--learning-origin', origin, '--operator-token-file', token]
        result = run_cli(bridge_cli, [*arguments, 'apply', original['id']], environment, tmp_path, success=False)
        persisted = run_cli(content_cli, ['operation', 'show', original['id']], environment, tmp_path)
        before = deepcopy(persisted)
        assert inspect_rows(case, 'SELECT count(*) FROM learning.cards')[0][0] == 0
        if scenario in ('unsupported', 'empty'):
            assert isinstance(result, subprocess.CompletedProcess) and result.returncode == 2
            assert persisted['state'] == ('approved' if scenario == 'empty' else 'failed'), persisted
            with psycopg.connect(environment['TEMPO_CONTENT_DATABASE_URL']) as database:
                assert database.execute('SELECT count(*) FROM content.sources').fetchone()[0] == 0
            assert inspect_rows(case, 'SELECT count(*) FROM learning.accepted_publications')[0][0] == 0
            if scenario == 'empty':
                assert run_cli(content_cli, ['operation', 'cancel', original['id']], environment, tmp_path)['cancelled']
            revision = 0
        else:
            if scenario == 'lost_stage':
                assert persisted['state'] == 'applying' and len(observed) == 1
                remote = client.get('/internal/publications/operations/' + original['id'] + ':activate', headers=headers).json()
                assert remote['state'] == 'unknown'
                process.restart_service()
                completed = run_cli(bridge_cli, [*arguments, 'reconcile', original['id']], environment, tmp_path)
                assert completed['state'] == 'completed'
                assert inspect_rows(case, 'SELECT count(*) FROM learning.activation_receipts')[0][0] == 1
                evidence = Path(os.environ['TEMPO_LEARNING_TEST_BRIDGE_EVIDENCE'])
                evidence.mkdir(parents=True, exist_ok=True)
                (evidence / 'preactivation-lost_stage.json').write_text(json.dumps({'initial': before,
                    'remote_before_restart': remote, 'recovered': completed, 'observed': observed, 'requests': controls['requests']}, indent=2, default=str))
                return
            if scenario == 'lost_abort':
                assert persisted['state'] == 'applying' and len(observed) == 2
                process.restart_service()
                run_cli(bridge_cli, [*arguments, 'reconcile', original['id']], environment, tmp_path, success=False)
                persisted = run_cli(content_cli, ['operation', 'show', original['id']], environment, tmp_path)
            assert persisted['state'] == 'failed', persisted
            remote = client.get('/internal/publications/operations/' + original['id'] + ':activate', headers=headers).json()
            assert remote['state'] == 'failed'
            assert remote['request'] == persisted['document']['delivery']['request']
            assert remote['error']['code'] == 'publication_abandoned_before_execution'
            revision = run_cli(content_cli, ['source', 'show', source['source_key']], environment, tmp_path)['requested_revision']
            assert revision == 1
        process.restart_service()
        valid['title'] = 'Corrected source after ' + scenario
        corrected = approve_authored(content_cli, environment, tmp_path, valid, revision)
        completed = run_cli(bridge_cli, [*arguments, 'apply', corrected['id']], environment, tmp_path)
        assert completed['state'] == 'completed' and corrected['id'] != original['id']
        if scenario in ('stage_conflict', 'lost_abort'):
            stale = client.post('/internal/publications/v2/activate', json=persisted['document']['delivery']['request'], headers=headers)
            assert stale.status_code == 409
            assert client.post('/internal/publications/operations/' + original['id'] + ':activate/retry', json={}, headers=headers).status_code == 409
        assert inspect_rows(case, 'SELECT count(*) FROM learning.activation_receipts')[0][0] == 1
        assert run_cli(content_cli, ['operation', 'show', original['id']], environment, tmp_path)['state'] == ('cancelled' if scenario == 'empty' else 'failed')
        evidence = Path(os.environ['TEMPO_LEARNING_TEST_BRIDGE_EVIDENCE'])
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / ('preactivation-' + scenario + '.json')).write_text(json.dumps({'initial': before, 'recovered': persisted, 'corrected': completed, 'observed': observed, 'requests': controls['requests']}, indent=2, default=str))


@pytest.mark.parametrize('scenario', ['unsupported_successor', 'retired_empty', 'retirement_race'])
def test_A_installed_preflight_preserves_previous_serving_authority(case, authored, tmp_path, scenario):
    content_cli, bridge_cli, environment = authored
    token = tmp_path / 'operator-token'
    token.write_text(case.configuration.operator_token); token.chmod(0o600)
    source = source_document()
    first = approve_authored(content_cli, environment, tmp_path, source, 0)
    headers = {'Authorization': 'Bearer ' + case.configuration.operator_token}
    with server(case, tmp_path) as (client, process):
        endpoint = str(client.base_url).rstrip('/')
        direct = ['--content-cli', content_cli, '--learning-origin', endpoint, '--operator-token-file', token]
        first = run_cli(bridge_cli, [*direct, 'apply', first['id']], environment, tmp_path)
        publication_id = first['document']['export']['publication']['publication_id']
        previous = client.get('/internal/publications/' + publication_id + '/status', headers=headers).json()
        successor = deepcopy(source)
        successor['title'] = scenario
        if scenario == 'unsupported_successor':
            successor['exercises'][0]['specification'] = {'type': 'explanation', 'rubric': 'Explain'}
        else:
            successor.update(routes=[], memberships=[], exercises=[], bindings=[])
        if scenario == 'retired_empty':
            response = client.post('/internal/publications/retire', json={'retirement_id': uuid.uuid4().hex,
                'publication_id': publication_id, 'publication_generation': previous['publication_generation'],
                'expected_activation_revision': previous['activation_revision']}, headers=headers)
            assert response.status_code == 200
        operation = approve_authored(content_cli, environment, tmp_path, successor, 1)
        with publication_fault_proxy(endpoint, 'retire_after_status' if scenario == 'retirement_race' else 'pass') as (origin, observed):
            arguments = ['--content-cli', content_cli, '--learning-origin', origin, '--operator-token-file', token]
            result = run_cli(bridge_cli, [*arguments, 'apply', operation['id']], environment, tmp_path, success=False)
            assert isinstance(result, subprocess.CompletedProcess) and result.returncode == 2
            persisted = run_cli(content_cli, ['operation', 'show', operation['id']], environment, tmp_path)
            revision = run_cli(content_cli, ['source', 'show', source['source_key']], environment, tmp_path)['requested_revision']
            assert revision == (2 if scenario == 'retirement_race' else 1)
            assert persisted['state'] == ('approved' if scenario == 'retired_empty' else 'failed')
            if scenario == 'unsupported_successor':
                status = client.get('/internal/publications/' + publication_id + '/status', headers=headers).json()
                assert (status['status'], status['activation_revision']) == ('active', previous['activation_revision'])
                assert inspect_rows(case, 'SELECT count(*) FROM learning.cards WHERE serving_active=1')[0][0] == 2
            if scenario == 'retirement_race':
                assert len(observed) == 1
                receipt = client.get('/internal/publications/operations/' + operation['id'] + ':retire', headers=headers).json()
                assert receipt['state'] == 'failed' and receipt['request']['expected_activation_revision'] == previous['activation_revision']
            if scenario == 'retired_empty':
                process.restart_service()
                assert run_cli(content_cli, ['operation', 'cancel', operation['id']], environment, tmp_path)['cancelled']
            source['title'] = 'Corrected after ' + scenario
            corrected = approve_authored(content_cli, environment, tmp_path, source, revision)
            corrected = run_cli(bridge_cli, [*arguments, 'apply', corrected['id']], environment, tmp_path)
            assert corrected['state'] == 'completed'
            evidence = Path(os.environ['TEMPO_LEARNING_TEST_BRIDGE_EVIDENCE'])
            evidence.mkdir(parents=True, exist_ok=True)
            (evidence / ('preflight-' + scenario + '.json')).write_text(json.dumps({'previous': previous,
                'original': persisted, 'corrected': corrected, 'observed': observed}, indent=2, default=str))


@pytest.mark.parametrize('action', ['apply', 'abandon'])
def test_A_installed_concurrent_operators_preserve_one_authority(case, authored, tmp_path, action):
    content_cli, bridge_cli, environment = authored
    token = tmp_path / 'operator-token'
    token.write_text(case.configuration.operator_token); token.chmod(0o600)
    operation = approve_authored(content_cli, environment, tmp_path, source_document(), 0)
    controls = {'mode': 'blocked_abort' if action == 'abandon' else 'pass'}
    with server(case, tmp_path) as (client, process), publication_fault_proxy(str(client.base_url).rstrip('/'), 'pass', controls, case.configuration.token) as (origin, observed):
        arguments = ['--content-cli', content_cli, '--learning-origin', origin, '--operator-token-file', token]
        if action == 'abandon':
            run_cli(bridge_cli, [*arguments, 'apply', operation['id']], environment, tmp_path, success=False)
            persisted = run_cli(content_cli, ['operation', 'show', operation['id']], environment, tmp_path)
            assert persisted['state'] == 'applying' and 'delivery' in persisted['document']
            controls['mode'] = 'pass'
        barrier = Barrier(2)
        def invoke():
            barrier.wait(timeout=5)
            return run_cli(bridge_cli, [*arguments, action, operation['id']], environment, tmp_path, success=False)
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = [future.result() for future in [executor.submit(invoke), executor.submit(invoke)]]
        persisted = run_cli(content_cli, ['operation', 'show', operation['id']], environment, tmp_path)
        assert persisted['state'] == ('completed' if action == 'apply' else 'failed'), outcomes
        assert inspect_rows(case, 'SELECT count(*) FROM learning.activation_receipts')[0][0] == int(action == 'apply')
        remote_id = operation['id'] + ':activate'
        receipt = client.get('/internal/publications/operations/' + remote_id, headers={'Authorization': 'Bearer ' + case.configuration.operator_token}).json()
        assert receipt['state'] == ('complete' if action == 'apply' else 'failed')
        process.restart_service()
        replay = run_cli(bridge_cli, [*arguments, action, operation['id']], environment, tmp_path, success=False)
        if action == 'apply': assert replay['document'] == persisted['document']
        else: assert isinstance(replay, subprocess.CompletedProcess) and replay.returncode == 2
        evidence = Path(os.environ['TEMPO_LEARNING_TEST_BRIDGE_EVIDENCE'])
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / ('concurrent-' + action + '.json')).write_text(json.dumps({'content': persisted, 'remote': receipt}, indent=2, default=str))


@pytest.mark.parametrize('remote_state', ['queued', 'complete'])
def test_A_installed_completion_cannot_substitute_checkpointed_request(case, authored, tmp_path, remote_state):
    from test_content_bridge import content_bundle, request_for
    content_cli, bridge_cli, environment = authored
    token = tmp_path / 'operator-token'
    token.write_text(case.configuration.operator_token); token.chmod(0o600)
    source = source_document()
    operation = approve_authored(content_cli, environment, tmp_path, source, 0)
    headers = {'Authorization': 'Bearer ' + case.configuration.operator_token}
    with server(case, tmp_path) as (client, process):
        origin = str(client.base_url).rstrip('/')
        arguments = ['--content-cli', content_cli, '--learning-origin', origin, '--operator-token-file', token]
        completed = run_cli(bridge_cli, [*arguments, 'apply', operation['id']], environment, tmp_path)
        publication_id = completed['document']['export']['publication']['publication_id']
        source.update(title='Retirement with mismatched remote receipt', routes=[], memberships=[], exercises=[], bindings=[])
        retirement = approve_authored(content_cli, environment, tmp_path, source, 1)
        run_cli(content_cli, ['operation', 'prepare', retirement['id']], environment, tmp_path)
        request = {'publication_id': publication_id, 'publication_generation': 1, 'expected_activation_revision': 1,
            'retirement_id': retirement['id'] + ':retire'}
        checkpoint = tmp_path / 'checkpoint.json'
        checkpoint.write_text(json.dumps({'delivery': {'endpoint': origin, 'request': request, 'retirement': True, 'due_date': '2026-10-16'}}))
        run_cli(content_cli, ['operation', 'checkpoint', retirement['id'], '--file', checkpoint], environment, tmp_path)
        other = content_bundle()
        assert other['publication']['publication_id'] != publication_id
        assert client.post('/internal/publications/v2/stage', json=other, headers=headers).status_code == 200
        assert client.post('/internal/publications/v2/activate', json=request_for(other), headers=headers).status_code == 200
        wrong_request = {**request, 'publication_id': other['publication']['publication_id']}
        if remote_state == 'queued':
            # Persist the genuine HTTP intent, then time out only its execution
            # at the real publication lock. No remote receipt is fabricated.
            with psycopg.connect(case.admin) as blocker:
                blocker.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                    ('learning:publication:' + other['publication']['publication_id'],))
                assert client.post('/internal/publications/retire', json=wrong_request, headers=headers).status_code == 503
        else:
            assert client.post('/internal/publications/retire', json=wrong_request, headers=headers).status_code == 200
        remote = client.get('/internal/publications/operations/' + request['retirement_id'], headers=headers).json()
        assert remote['state'] == remote_state and remote['request'] != request
        if remote_state == 'complete':
            proposed = tmp_path / 'wrong-completion.json'
            proposed.write_text(json.dumps({'transport': 'authenticated_learning_http', 'endpoint': origin,
                'acknowledgement': {'contract_version': 1, 'workspace_id': 'workspace-a', 'retirement_id': request['retirement_id'],
                    'publication_id': publication_id, 'publication_generation': 1, 'reason': None, **remote['response']}}))
            denied = run_cli(content_cli, ['operation', 'record', retirement['id'], '--file', proposed,
                '--learning-origin', origin, '--operator-token-file', token], environment, tmp_path, success=False)
            assert isinstance(denied, subprocess.CompletedProcess) and denied.returncode == 2
        for command in ('reconcile', 'abandon'):
            result = run_cli(bridge_cli, [*arguments, command, retirement['id']], environment, tmp_path, success=False)
            assert isinstance(result, subprocess.CompletedProcess) and result.returncode == 2
            persisted = run_cli(content_cli, ['operation', 'show', retirement['id']], environment, tmp_path)
            assert persisted['state'] == 'applying' and 'receipt' not in persisted['document']
            actual = client.get('/internal/publications/operations/' + request['retirement_id'], headers=headers).json()
            assert actual == remote
        assert client.get('/internal/publications/' + publication_id + '/status', headers=headers).json()['status'] == 'active'
        other_status = client.get('/internal/publications/' + other['publication']['publication_id'] + '/status', headers=headers).json()
        assert other_status['status'] == ('active' if remote_state == 'queued' else 'retired')
        evidence = Path(os.environ['TEMPO_LEARNING_TEST_BRIDGE_EVIDENCE'])
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / ('conflicting-authority-' + remote_state + '.json')).write_text(json.dumps({'content': persisted, 'remote': remote, 'other_serving': other_status}, indent=2, default=str))
