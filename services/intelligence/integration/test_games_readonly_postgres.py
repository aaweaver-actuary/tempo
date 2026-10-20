"""Focused installed reader composition against invocation-owned PostgreSQL only."""
from dataclasses import replace
from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
import pytest
from tempo_intelligence import create_app
from tempo_intelligence.store import Store
from .test_analysis_postgres import setup

TABLES=('analysis_inputs','analysis_pages','analysis_runs','analysis_heads','analysis_positions',
        'analysis_executions','analysis_results','analysis_reports','analysis_report_items',
        'analysis_dismissals','analysis_commands','model_profiles','requests','request_heads',
        'caches','evidence','commands','maia_jobs','credential_head','credential_registrations','provider_control')


def snapshot(store):
    with store.transaction(read_only=True) as database:
        return {name:database.execute(sql.SQL("SELECT COALESCE(jsonb_agg(to_jsonb(row) ORDER BY to_jsonb(row)::text),'[]'::jsonb) AS rows FROM {} row").format(sql.Identifier(name))).fetchone()['rows'] for name in TABLES}


def test_games_readonly_http_and_transactions_preserve_all_state(state):
    mutable=setup(state,budget=0);before=snapshot(state[0])
    configuration=replace(state[1],read_only=True,worker_enabled=False,model_profiles=())
    reader=Store(configuration)
    try:
        app=create_app(configuration,store=reader)
        with TestClient(app) as client:
            headers={'Authorization':'Bearer '+configuration.caller_token,'X-Tempo-Workspace':'fixture'}
            capabilities=client.get('/v1/analysis/bootstrap',headers=headers).json()['capabilities']
            assert 'games-readonly-v1' in capabilities
            assert app.state.worker is None
            assert client.get('/v1/analysis/runs',headers=headers).status_code==200
            run=client.get('/v1/analysis/runs/run',headers=headers).json()
            first=client.get('/v1/analysis/runs/run/games?limit=1',headers=headers).json()
            assert first['input_id']==run['scope']['input_id'] and first['input_revision']==run['scope']['input_revision']
            assert first['next_after']
            rest=client.get('/v1/analysis/runs/run/games',params={'after':first['next_after']},headers=headers).json()
            expected=mutable.games('run')
            assert first['items']+rest['items']==expected['items'] and first['game_count']==expected['game_count']
            for path in ('/v1/analysis/runs','/v1/analysis/runs/run/commands','/v1/analysis/findings/finding/commands'):
                assert client.post(path,json={'unsafe':'must not be validated'},headers=headers).status_code==405
            for path,token in (('/v1/analysis/inputs',configuration.operator_token),('/v1/maia/results',configuration.worker_token)):
                assert client.post(path,json={},headers={**headers,'Authorization':'Bearer '+token}).status_code==405
            assert client.get('/v1/analysis/runs/run/games',headers={**headers,'X-Tempo-Workspace':'foreign'}).status_code==403
            assert client.get('/v1/analysis/runs/run/games?limit=11',headers=headers).status_code==422
            # Even an internal caller omitting read_only cannot write through either pool.
            for background in (False,True):
                with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                    with reader.transaction(background=background) as database:
                        assert database.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']=='on'
                        assert database.execute('SHOW statement_timeout').fetchone()['statement_timeout']=='1500ms'
                        assert database.execute('SHOW transaction_timeout').fetchone()['transaction_timeout']=='3s'
                        database.execute("UPDATE analysis_runs SET state='cancelled' WHERE run_id='run'")
        assert snapshot(state[0])==before
    finally:
        reader.close()
