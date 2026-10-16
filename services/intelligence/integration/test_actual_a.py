"""Explicit installed A export -> C acceptance -> installed B bounded execution."""
from dataclasses import replace
import json
import os
import secrets
from pathlib import Path
import psycopg
from psycopg.conninfo import make_conninfo
from psycopg import sql
from fastapi.testclient import TestClient
from tempo_intelligence import create_app,analysis_v1,run_analysis_work
from tempo_intelligence.migrate import migrate
from tempo_intelligence.store import Store
from tempo_intelligence.analysis.store import AnalysisStore
from tempo_intelligence.errors import DomainError
from tempo_analysis_pilot import normalize,connect


def test_actual_A_export_and_B_dispatch_through_C(state):
    directory=Path(os.environ['TEMPO_C_A_EXPORT']);ids=json.loads(Path(os.environ['TEMPO_C_GAME_IDS_FILE']).read_text())
    original=json.loads((directory/'manifest.json').read_text())
    manifest,pages=normalize(directory,workspace=original['workspace_id'],selected_game_ids=ids)
    assert original['provenance']['kind']=='synthetic'  # This named lane is the declared installed rehearsal.
    runtime_password=secrets.token_urlsafe(32)
    with psycopg.connect(os.environ['TEMPO_INTELLIGENCE_TEST_ADMIN_URL'],autocommit=True) as db:
        db.execute(sql.SQL('CREATE ROLE intelligence_actual_a_runtime LOGIN PASSWORD {} NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS').format(sql.Literal(runtime_password)))
        db.execute('CREATE DATABASE intelligence_actual_a OWNER intelligence_owner')
        db.execute('REVOKE ALL ON DATABASE intelligence_actual_a FROM PUBLIC')
        db.execute('GRANT CONNECT ON DATABASE intelligence_actual_a TO intelligence_actual_a_runtime')
    owner_url=make_conninfo(os.environ['TEMPO_INTELLIGENCE_TEST_OWNER_URL'],dbname='intelligence_actual_a')
    runtime_url=make_conninfo(os.environ['TEMPO_INTELLIGENCE_TEST_RUNTIME_URL'],dbname='intelligence_actual_a',user='intelligence_actual_a_runtime',password=runtime_password)
    migrate(owner_url,original['workspace_id'],'intelligence_actual_a_runtime')
    configuration=replace(state[1],workspace_id=original['workspace_id'],database_url=runtime_url)
    store=Store(configuration)
    try:
        store.validate_startup();owner=AnalysisStore(store)
        with TestClient(create_app(configuration,store=store)) as client:
            client.headers.update({'Authorization':'Bearer '+configuration.operator_token,'X-Tempo-Workspace':configuration.workspace_id})
            assert client.post('/v1/analysis/inputs',json=manifest).status_code==200
            for page in pages:assert client.post('/v1/analysis/inputs/pages',json=page).status_code==200
            assert client.post('/v1/analysis/inputs/'+manifest['input_id']+'/finalize',json={'schema_version':1,'command_id':'actual-A-finalize','action':'finalize','expected_revision':manifest['revision']}).status_code==200
            client.headers['Authorization']='Bearer '+configuration.caller_token
            request={'schema_version':1,'workspace_id':configuration.workspace_id,'run_id':'actual-A-B','revision':1,'input_id':manifest['input_id'],'input_revision':manifest['revision'],'position_budget':250}
            assert client.post('/v1/analysis/runs',json=request).status_code==200
            owner.derive('actual-A-B');report=client.get('/v1/analysis/runs/actual-A-B/report').json()
            misses=[item for item in report['items'] if item['category']=='repertoire_miss'];gaps=[item for item in report['items'] if item['category']=='practical_gap']
            assert len(misses)==1 and set(misses[0]['expected_moves'])=={'e2e4','d2d4'}
            assert misses[0]['delivery_status']=='completed' and misses[0]['learning_settlement']=='unknown'
            assert len(gaps)==1 and gaps[0]['opponent_move']=='c7c5' and gaps[0]['eligible_encounter_games']==2 and gaps[0]['actual_frequency']==0.5
        worker_outcome='accepted_result'
        try:run_analysis_work(configuration,'actual-A-B',8,client=connect(Path(os.environ['TEMPO_C_STOCKFISH_PROFILE'])))
        except DomainError as error:
            assert error.code=='analysis_worker_failed'
            with store.transaction(read_only=True) as db:
                events=db.execute('SELECT payload FROM analysis_execution_events').fetchall()
            assert len(events)==1 and events[0]['payload']['model']=='JobStatus' and events[0]['payload']['payload']['status']=='failed'
            worker_outcome='typed_worker_failure'
        owner.derive('actual-A-B');report=owner.report('actual-A-B')
        with store.transaction(read_only=True) as db:
            executions=db.execute('SELECT payload FROM analysis_executions').fetchall()
            accepted=db.execute('SELECT payload FROM analysis_results').fetchall()
            events=db.execute('SELECT payload FROM analysis_execution_events').fetchall()
        assert 1<=len(executions)<=8 and (accepted or events)
        output=Path(os.environ['TEMPO_C_BROWSER_FIXTURE_FILE']).parent/'actual-A-B.json'
        output.write_text(json.dumps({'case':'C_actual_A_B_durable','passed':True,'synthetic_games':True,'A_manifest_digest':original['manifest_digest'],'worker_outcome':worker_outcome,'executions':executions,'accepted':accepted,'events':events,'report':report},indent=2)+'\n')
    finally:store.close()
