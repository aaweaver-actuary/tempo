"""Safe session sharing, real Redis CAS/expiry and absent durable credentials."""
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app import postgres_store
from app.services import explorer_sessions as sessions


def proof_explorer_sessions(parent_database_url):
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Explorer session proof requires disposable fixtures')
    import check_postgres_graph_retention as fixtures
    from fastapi.testclient import TestClient
    from app.main import app
    started=time.monotonic()
    with patch.object(fixtures,'DATABASE_URL',parent_database_url), fixtures.owned_fixture_database() as identity, \
            patch.object(sessions,'TOKEN_KEY',identity+':token'), patch.object(sessions,'REJECTED_KEY',identity+':rejected'), \
            patch.dict(os.environ,{'TEMPO_LICHESS_EXPLORER_TOKEN':''}):
        client=TestClient(app)
        path='/api/repertoire-coverage/explorer-session'
        server=sessions.client()
        try:
            assert sessions.status()=={'status':'registration_missing'}
            first='synthetic-session-'+identity
            replacement='synthetic-reconnected-'+identity
            headers={'Authorization':'Bearer '+first,'X-Tempo-Work-Class':'background'}
            assert client.post(path,headers=headers).json()=={'registered':True}
            assert client.get(path,headers=headers).json()=={'status':'available'}
            assert 86300<server.ttl(sessions.TOKEN_KEY)<=86400
            assert server.config_get('save','appendonly')=={'save':'','appendonly':'no'}
            # A separate backend process has no process-local credential state.
            script="""import os
from app.services import explorer_sessions as sessions
sessions.TOKEN_KEY=os.environ['TEMPO_SESSION_PROOF_TOKEN_KEY']
sessions.REJECTED_KEY=os.environ['TEMPO_SESSION_PROOF_REJECTED_KEY']
assert sessions.get_token()==os.environ['TEMPO_SESSION_PROOF_EXPECTED_TOKEN']
print('PASS Explorer credential is available to a separate backend process')
"""
            result=subprocess.run([sys.executable,'-c',script],env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'backend'),
                'TEMPO_SESSION_PROOF_TOKEN_KEY':sessions.TOKEN_KEY,'TEMPO_SESSION_PROOF_REJECTED_KEY':sessions.REJECTED_KEY,
                'TEMPO_SESSION_PROOF_EXPECTED_TOKEN':first},capture_output=True,text=True)
            assert result.returncode==0,'Separate session reader failed'
            # No credentials or fingerprints are printed in proof diagnostics.
            print(result.stdout.strip())
            sessions.register(replacement)
            assert not sessions.reject(first),'Late rejection removed the replacement'
            assert sessions.get_token()==replacement
            assert sessions.reject(replacement)
            rejected_headers={'Authorization':'Bearer '+replacement,'X-Tempo-Work-Class':'background'}
            assert client.get(path,headers=rejected_headers).json()=={'status':'credential_rejected'}
            rejected=client.post(path,headers=rejected_headers)
            assert rejected.status_code==409 and replacement not in rejected.text
            assert client.get(path,headers=headers).json()=={'status':'registration_missing'},'A different credential inherited another credential\'s rejection'
            assert client.post(path,headers=headers).json()=={'registered':True}
            sessions._client.cache_clear()  # Backend restart loses clients, not the shared memory session.
            assert sessions.get_token()==first
            server.delete(sessions.TOKEN_KEY)
            assert client.get(path,headers=headers).json()=={'status':'registration_missing'}
            assert client.post(path,headers=headers).json()=={'registered':True},'Unchanged valid browser credential did not restore lost registration'
            assert sessions.get_token()==first
            with postgres_store.connection(read_only=True) as database:
                assert database.execute_native('SELECT COUNT(*) FROM background_tasks WHERE payload_json LIKE %s',('%'+first+'%',)).fetchone()[0]==0
                assert database.execute_native('SELECT COUNT(*) FROM operation_receipts WHERE payload_json LIKE %s OR response_json LIKE %s',('%'+first+'%','%'+first+'%')).fetchone()[0]==0
            from app.services.redis_admission_gate import client as broker_client
            assert broker_client().get('tempo:coverage:explorer-session-token') is None
            print('PASS test_postgres_explorer_ephemeral_session_process_restart_rejection_cas_and_missing_registration_recovery; schema46; 24-hour expiry; no PostgreSQL/broker credentials')
        finally:
            server.delete(sessions.TOKEN_KEY,sessions.REJECTED_KEY)
    print('Native proof duration:',round(time.monotonic()-started,2),'seconds')


def prove_store_recreation(mode):
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Session recreation requires a disposable stack')
    # This namespace is never read by application/provider workers. The proof
    # credential is synthetic and is never emitted or put in durable storage.
    with patch.object(sessions, 'TOKEN_KEY', 'tempo:session-recreation-proof:token'), \
            patch.object(sessions, 'REJECTED_KEY', 'tempo:session-recreation-proof:rejected'), \
            patch.dict(os.environ, {'TEMPO_LICHESS_EXPLORER_TOKEN': ''}):
        if mode == '--seed-recreation':
            sessions.register('synthetic-browser-session-recreation-proof')
            assert sessions.status() == {'status': 'available'}
        else:
            assert sessions.status() == {'status': 'registration_missing'}, 'Session persisted across container recreation'
            sessions.register('synthetic-browser-session-recreation-proof')
            assert sessions.get_token() == 'synthetic-browser-session-recreation-proof'
            sessions.client().delete(sessions.TOKEN_KEY, sessions.REJECTED_KEY)
            print('PASS test_explorer_dedicated_store_container_recreation_loses_session_and_unchanged_browser_restores_it')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] in {'--seed-recreation', '--verify-recreation'}:
        prove_store_recreation(sys.argv[1])
    else:
        proof_explorer_sessions(os.environ['TEMPO_EXPLORER_PROOF_URL'])
