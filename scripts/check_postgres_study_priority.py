"""Real study-priority proof. Standalone execution owns every container it uses."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'scripts'))
CASES = ['test_postgres_priority_unlock_rejected_pages_and_restart',
         'test_postgres_priority_recovery_ignores_terminal_history_and_future_work',
         'test_postgres_priority_real_review_replay_and_loaded_latency',
         'test_postgres_priority_control_poll_does_not_wait_for_database',
         'test_postgres_priority_sessions_expire_independently_and_stale_control_denies_optional',
         'test_postgres_priority_index_only_rollback_preserves_accepted_reviews']
FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'


def docker(*arguments, environment=None):
    result = subprocess.run(['docker', *arguments], check=False, capture_output=True, text=True,
                            env=environment, timeout=60)
    if result.returncode:
        raise RuntimeError(f'Docker {arguments[0]} failed: {result.stderr[:400]}')
    return result.stdout.strip()


def percentiles(samples):
    ordered = sorted(samples)
    return {f'p{percent}': ordered[min(len(ordered)-1, int(len(ordered)*percent/100))]
            for percent in (50, 95, 99)}


def api_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


@contextmanager
def private_database(parent_url):
    identity = 'tempo_study_priority_' + uuid4().hex
    with psycopg.connect(parent_url, autocommit=True) as parent:
        # An environment flag alone never establishes disposable ownership.
        comment = parent.execute("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()").fetchone()[0]
        if comment != 'tempo-disposable-postgres-test':
            raise RuntimeError('Refusing a parent database without the disposable-runner marker')
        parent.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(identity)))
    try:
        yield make_conninfo(parent_url, dbname=identity)
    finally:
        with psycopg.connect(parent_url, autocommit=True) as parent:
            parent.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(identity)))


def advance_unlock_checkpoint():
    from app import postgres_store
    from app.services import queue_application
    from app.services.postgres_queue_refresh import _ELIGIBILITY_PHASES
    with postgres_store.connection(background=True) as database:
        row=database.execute("SELECT payload_json FROM background_tasks WHERE id='priority-unlock-checkpoint' AND generation=1 AND lease_token='priority-proof' AND state='leased' FOR UPDATE").fetchone()
        assert row is not None
        payload=json.loads(row[0])
        cursor=queue_application._unlock_eligible_opening_cards(database,date.today().isoformat(),after_card_id=payload.get('after_card_id',''),batch_size=8)
        payload={'queue_date':date.today().isoformat(),'_queue_phase':'unlock_opening' if cursor else _ELIGIBILITY_PHASES[1],**({'after_card_id':cursor} if cursor else {})}
        assert database.execute("UPDATE background_tasks SET payload_json=?,phase=? WHERE id='priority-unlock-checkpoint' AND generation=1 AND lease_token='priority-proof' AND state='leased'",(json.dumps(payload),payload['_queue_phase'])).rowcount==1
        return cursor


def run_proof(parent_url, redis_url, evidence, *, graph_rows=1_400_000, receipt_rows=415_000, postgres_container=None):
    from apply_postgres_migrations import apply_migrations
    with private_database(parent_url) as owned_url:
        apply_migrations(owned_url)
        reader = 'priority_reader_' + secrets.token_hex(8)
        reader_password = secrets.token_urlsafe(24)
        configuration = dict(os.environ)
        configuration.update(TEMPO_DATABASE_READ_URL=owned_url, TEMPO_DATABASE_WRITE_URL=owned_url,
            TEMPO_REDIS_URL=redis_url, TEMPO_ADMISSION_NAMESPACE='tempo:priority:' + uuid4().hex,
            TEMPO_REDIS_NAMESPACE='tempo:priority:' + uuid4().hex + ':', TEMPO_COORDINATOR_MODE='external',
            TEMPO_TRAINING_TIMINGS='1', TEMPO_TEST_INSTANCE='disposable', PYTHONPATH=str(ROOT / 'backend'))
        original = dict(os.environ)
        os.environ.update(configuration)
        from app import main, postgres_store
        from app.command_gateway import claim_recoverable_operation
        from app.services import queue_application, redis_admission_gate, study_priority
        from app.services.background_runtime import measure_training_timing
        from unittest.mock import patch
        saved_admission_keys = redis_admission_gate._FOREGROUND_KEY, redis_admission_gate._BACKGROUND_KEY
        saved_policy_keys = study_priority.POLICY_KEY, study_priority.GENERATION_KEY
        redis_admission_gate._FOREGROUND_KEY = configuration['TEMPO_ADMISSION_NAMESPACE'] + ':foreground'
        redis_admission_gate._BACKGROUND_KEY = configuration['TEMPO_ADMISSION_NAMESPACE'] + ':background'
        study_priority.POLICY_KEY = configuration['TEMPO_ADMISSION_NAMESPACE'] + ':study-policy'
        study_priority.GENERATION_KEY = study_priority.POLICY_KEY + ':generation'
        redis_admission_gate.client.cache_clear(); study_priority.control_client.cache_clear()
        processes = []
        handles = []
        results = {}; stop_load = threading.Event(); loader = None
        try:
            with psycopg.connect(owned_url) as database:
                database.execute('INSERT INTO settings(id,study_only_mode) VALUES(1,0)')
                database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('priority','Priority','synthetic',%s)", (date.today().isoformat(),))
                database.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(reader), sql.Literal(reader_password)))
                database.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(reader)))
                database.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}').format(sql.Identifier(reader)))
                database.execute(sql.SQL('ALTER ROLE {} SET default_transaction_read_only=on').format(sql.Identifier(reader)))
                database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type) SELECT 'locked-'||lpad(i::text,6,'0'),'priority','prefix',%s,'[\"e2e4\"]','locked',%s,'opening' FROM generate_series(1,2048) i", (FEN, date.today().isoformat()))
                # Ineligible candidates still advance; no parent-wide query is needed.
            with psycopg.connect(owned_url) as database:
                database.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES('load-line','priority','Load','white',%s,'[\"e2e4\"]',%s)", (FEN, date.today().isoformat()))
            for start in range(0, graph_rows, 5_000):
                with psycopg.connect(owned_url) as database:
                    database.execute("INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color) SELECT 'priority',1,'load-line',i,'locked-'||lpad((1+(i%%2048))::text,6,'0'),'unpracticed',%s,%s,'[\"e2e4\"]','white' FROM generate_series(%s::bigint,%s::bigint) i", (FEN, FEN, start, min(graph_rows-1,start+4_999)))
            with psycopg.connect(owned_url) as database:
                database.execute("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,payload_json) SELECT 'history-'||i,'cards.review','synthetic','complete','{}' FROM generate_series(1,%s) i", (receipt_rows,))
                database.execute("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,payload_json,next_retry_at,lease_expires_at,updated_at) SELECT 'future-'||i,'cards.review','synthetic','retrying','{}',NOW()+INTERVAL '1 day',NULL,NOW() FROM generate_series(1,5000) i")
                for index, state in enumerate(('queued','executing','retrying')):
                    database.execute("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,payload_json,next_retry_at,lease_expires_at,updated_at) VALUES(%s,'cards.review','synthetic',%s,'{}',NOW()-INTERVAL '1 minute',NOW()-INTERVAL '1 minute',NOW()-INTERVAL '1 minute')", ('due-'+str(index),state))
                database.execute('ANALYZE operation_receipts'); database.execute('ANALYZE opening_graph_steps'); database.execute('ANALYZE cards')
            with psycopg.connect(owned_url) as database:
                database.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES('priority',1,'ready',%s)",(date.today().isoformat(),))
            with psycopg.connect(owned_url) as database:
                database.execute("INSERT INTO background_tasks(id,kind,deduplication_key,state,phase,payload_json,next_attempt_at,created_at,updated_at,lease_token) VALUES('priority-unlock-checkpoint','daily_queue','priority-unlock','leased','unlock_opening',%s,%s,%s,%s,'priority-proof')",(json.dumps({'queue_date':date.today().isoformat(),'_queue_phase':'unlock_opening'}),*[date.today().isoformat()]*3))
            cursor = ''
            pages = 0
            while cursor is not None:
                cursor = advance_unlock_checkpoint()
                pages += 1
                if pages == 2:
                    postgres_store.close_pools()
                    resumed=subprocess.run([sys.executable,'-c','import json; from check_postgres_study_priority import advance_unlock_checkpoint; print(json.dumps(advance_unlock_checkpoint()))'],cwd=ROOT/'scripts',env=configuration,check=True,capture_output=True,text=True,timeout=20)
                    cursor=json.loads(resumed.stdout)
                    assert cursor == 'locked-000048'
                    pages += 1
                assert pages <= 129
            with psycopg.connect(owned_url) as database:
                database.execute("DELETE FROM background_tasks WHERE id='priority-unlock-checkpoint' AND lease_token='priority-proof'")
            assert pages == 128
            with psycopg.connect(owned_url) as database:
                database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type) VALUES('z-root','priority','prefix',%s,'[\"e2e4\"]','locked',%s,'opening')",(FEN,date.today().isoformat()))
                database.execute("INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color) VALUES('priority',1,'load-line',%s,'z-root',NULL,%s,%s,'[\"e2e4\"]','white')",(graph_rows+1,FEN,FEN))
                database.execute("UPDATE opening_graph_publications SET state='failed'")
            with postgres_store.connection(background=True) as database:
                assert queue_application._unlock_eligible_opening_cards(database,date.today().isoformat(),after_card_id='z-previous',batch_size=8) is None
            with psycopg.connect(owned_url) as database:
                assert database.execute("SELECT state FROM cards WHERE id='z-root'").fetchone()[0] == 'locked'
                database.execute("UPDATE opening_graph_publications SET state='ready'")
            with postgres_store.connection(background=True) as database:
                queue_application._unlock_eligible_opening_cards(database,date.today().isoformat(),after_card_id='z-previous',batch_size=8)
            with psycopg.connect(owned_url) as database:
                assert database.execute("SELECT state FROM cards WHERE id='z-root'").fetchone()[0] == 'new'
            results[CASES[0]] = {'pages': pages, 'examined': 2048, 'restart_after_page': 2, 'fresh_process_resumed_durable_checkpoint':True, 'published_graph_steps':graph_rows}
            timings = []
            recovered = []
            for _ in range(4):
                started = time.perf_counter()
                operation = claim_recoverable_operation()
                timings.append((time.perf_counter()-started)*1000)
                if operation: recovered.append(operation['operation_id'])
            assert sorted(recovered) == ['due-0','due-1','due-2']
            assert max(timings) < 250, timings
            results[CASES[1]] = {'terminal_receipts': receipt_rows, 'future_receipts': 5000,
                                 'claimed': recovered, 'duration_ms': timings}
            # Existing roles and existing parent data are never altered.
            reader_url = make_conninfo(owned_url, user=reader, password=reader_password)
            port = api_port(); origin = f'http://127.0.0.1:{port}'
            for label, command, overrides in (
                ('foreground', [sys.executable,'-m','celery','-A','app.celery_app:celery_app','worker','--queues=foreground','--pool=solo','--concurrency=1','--prefetch-multiplier=1','--loglevel=INFO'], {}),
                ('api', [sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port)],
                 {'TEMPO_DATABASE_READ_URL':reader_url,'TEMPO_DATABASE_WRITE_URL':None}),
            ):
                log = open(evidence / (label+'.log'), 'w'); handles.append(log)
                child_environment = {**configuration, **overrides}
                child_environment = {key:value for key,value in child_environment.items() if value is not None}
                processes.append(subprocess.Popen(command,cwd=ROOT,env=child_environment,stdout=log,stderr=subprocess.STDOUT))
            with psycopg.connect(owned_url) as database:
                database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type) SELECT 'review-'||lpad(i::text,4,'0'),'priority','prefix',%s,'[\"e2e4\"]','learning',%s,'tactic' FROM generate_series(1,120) i", (FEN,date.today().isoformat()))
                database.execute("INSERT INTO daily_queue(queue_date,card_id,position) SELECT %s,id,row_number() OVER(ORDER BY id) FROM cards WHERE id LIKE 'review-%%'",(date.today().isoformat(),))
                database.execute("INSERT INTO queue_projections(queue_date,state,generation,refresh_pending) VALUES(%s,'ready',1,0)",(date.today().isoformat(),))
            with httpx.Client(base_url=origin,timeout=20) as client:
                deadline = time.monotonic()+30
                while True:
                    try:
                        if client.get('/api/queue/window?limit=2').status_code == 200: break
                    except httpx.TransportError: pass
                    assert time.monotonic() < deadline, 'Owned study assembly did not become ready'
                    time.sleep(0.1)
                samples = {'idle': [], 'loaded': []}; replay_samples = []
                server_samples = []; network_samples = []; priority_yield = []; progress = [0]
                stop_load = threading.Event(); load_started = threading.Event(); load_idle = threading.Event()
                def background_load():
                    from app.database import background_connection
                    from app.services.activity_gate import BackgroundAdmissionDeferred, activity_gate
                    while not stop_load.is_set():
                        try:
                            with activity_gate.background_job('study_priority_rehearsal','owned-load',yielding=True):
                                with background_connection() as database:
                                    load_idle.clear(); load_started.set()
                                    database.execute_native('SELECT pg_sleep(0.02)')
                                    progress[0] += 1
                            load_idle.set()
                        except BackgroundAdmissionDeferred:
                            load_idle.set(); stop_load.wait(0.01)
                        except Exception:
                            load_idle.set(); raise
                loader = None
                for index in range(60):
                    if index == 30:
                        loader = threading.Thread(target=background_load,daemon=True); loader.start()
                        assert load_started.wait(5), 'Loaded run did not execute actual background SQL'
                        priority_started = time.perf_counter()
                        activity = client.post('/api/system/browser-activity?session_id=rehearsal',headers={'X-Tempo-Work-Class':'background'})
                        assert activity.status_code == 200
                        assert load_idle.wait(0.25), 'Background section did not yield'
                        priority_yield.append((time.perf_counter()-priority_started)*1000)
                    if index >= 30:
                        client.post('/api/system/browser-activity?session_id=rehearsal',headers={'X-Tempo-Work-Class':'background'})
                    queue = client.get('/api/queue/window?limit=2').json()
                    first, second = queue['cards'][:2]
                    attempt = uuid4().hex
                    payload = {'outcome':'correct','queue_entry_id':first['queue_entry_id'],
                        'expected_revision':first['revision'],'attempt_id':attempt,
                        'prepared_next':{'queue_entry_id':second['queue_entry_id'],'card_id':second['id'],
                            'revision':second['revision'],'cycle':second['cycle'],'local_date':queue['local_date']}}
                    started = time.perf_counter()
                    response = client.post('/api/cards/'+first['id']+'/review',json=payload,headers={'Idempotency-Key':'review-attempt:'+attempt})
                    assert response.status_code == 200, response.text
                    saved = response.json(); assert saved['persisted'] and saved['prepared_continuation']['card_id'] == second['id']
                    confirmed_ms = (time.perf_counter()-started)*1000
                    server_ms = float(response.headers['X-Tempo-Clock-Finished-Ms'])-float(response.headers['X-Tempo-Clock-Started-Ms'])
                    server_samples.append(server_ms); network_samples.append(max(0,confirmed_ms-server_ms))
                    samples['idle' if index < 30 else 'loaded'].append(confirmed_ms)
                    replay_started = time.perf_counter()
                    replay = client.post('/api/cards/'+first['id']+'/review',json=payload,headers={'Idempotency-Key':'review-attempt:'+attempt})
                    assert replay.status_code == 200 and replay.json() == saved
                    replay_samples.append((time.perf_counter()-replay_started)*1000)
                with psycopg.connect(owned_url) as database:
                    assert database.execute("SELECT COUNT(*) FROM reviews WHERE card_id LIKE 'review-%%'").fetchone()[0] == 60
                before_quiet = progress[0]
                deadline = time.monotonic()+5
                while progress[0] == before_quiet and time.monotonic() < deadline: time.sleep(0.05)
                stop_load.set()
                if loader: loader.join(timeout=1)
                assert progress[0] > before_quiet, 'Useful optional work did not return during quiet time'
                for lane in samples.values():
                    assert percentiles(lane)['p95'] <= 500 and percentiles(lane)['p99'] <= 1000, percentiles(lane)
                results[CASES[2]] = {'confirmed_save_ms': {lane:percentiles(values) for lane,values in samples.items()},
                    'server_request_ms':percentiles(server_samples),'transport_and_client_ms':percentiles(network_samples),
                    'replay_ms':percentiles(replay_samples),'samples':samples,'reviews':60,
                    'optional_yield_ms':priority_yield,'background_sections':progress[0]}
                assert max(priority_yield) <= 250
                control_times = []
                with psycopg.connect(owned_url) as locked:
                    locked.execute('LOCK TABLE settings IN ACCESS EXCLUSIVE MODE')
                    if postgres_container: docker('pause',postgres_container)
                    try:
                        for _ in range(10):
                            started = time.perf_counter()
                            response = client.get('/api/system/foreground-active',headers={'X-Tempo-Work-Class':'background'})
                            assert response.status_code == 200
                            control_times.append((time.perf_counter()-started)*1000)
                        assert max(control_times) < 250
                    finally:
                        if postgres_container: docker('unpause',postgres_container)
                results[CASES[3]] = {'database_paused':bool(postgres_container),'settings_locked':True,'control_ms':control_times}
                shared = study_priority.control_client()
                foreground_key = redis_admission_gate._FOREGROUND_KEY
                for session in ('device_a','device_b'):
                    assert client.post('/api/system/browser-activity?session_id='+session,headers={'X-Tempo-Work-Class':'background'}).status_code == 200
                shared.zadd(foreground_key,{'browser-activity:device_a':int(time.time()*1000)-1})
                snapshot = client.get('/api/system/foreground-active',headers={'X-Tempo-Work-Class':'background'}).json()
                assert snapshot['active'] is True and snapshot['available'] is True
                shared.zadd(foreground_key,{'browser-activity:device_b':int(time.time()*1000)-1})
                encoded = shared.get(study_priority.POLICY_KEY)
                expired = json.loads(encoded); expired['expires_at_ms'] = 0
                shared.set(study_priority.POLICY_KEY,json.dumps(expired),ex=15)
                stale = client.get('/api/system/foreground-active',headers={'X-Tempo-Work-Class':'background'}).json()
                assert stale['active'] is True and stale['available'] is False
                shared.set(study_priority.POLICY_KEY,encoded,ex=15)
                results[CASES[4]] = {'independent_devices':2,'expired_policy_denies_optional':True}
                def study_snapshot(database):
                    return tuple(database.execute("SELECT md5(COALESCE(string_agg(row_to_json(item)::text,',' ORDER BY item.id),'')) FROM "+table+" item").fetchone()[0]
                        for table in ('cards','reviews')) + (database.execute("SELECT COUNT(*) FROM operation_receipts").fetchone()[0],)
                with psycopg.connect(owned_url,autocommit=True) as database:
                    before_rollback = study_snapshot(database)
                    database.execute((ROOT/'backend/rollbacks/049_study_priority_recovery.sql').read_text())
                    assert database.execute('SELECT MAX(version) FROM tempo_schema_migrations').fetchone()[0] == 48
                    assert study_snapshot(database) == before_rollback
                apply_migrations(owned_url)
                with psycopg.connect(owned_url) as database:
                    assert study_snapshot(database) == before_rollback
                    assert database.execute('SELECT MAX(version) FROM tempo_schema_migrations').fetchone()[0] == 49
                results[CASES[5]] = {'schema_path':[49,48,49],'accepted_reviews_retained':60,'study_snapshot_unchanged':True}
            assert set(results) == set(CASES)
            return results
        finally:
            stop_load.set()
            if loader: loader.join(timeout=1)
            for process in reversed(processes):
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
            for handle in handles: handle.close()
            postgres_store.close_pools()
            redis_admission_gate.client.cache_clear(); study_priority.control_client.cache_clear()
            redis_admission_gate._FOREGROUND_KEY, redis_admission_gate._BACKGROUND_KEY = saved_admission_keys
            study_priority.POLICY_KEY, study_priority.GENERATION_KEY = saved_policy_keys
            os.environ.clear(); os.environ.update(original)
            with psycopg.connect(owned_url) as database:
                database.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(reader)))
            with psycopg.connect(parent_url,autocommit=True) as parent:
                parent.execute(sql.SQL('DROP ROLE IF EXISTS {}').format(sql.Identifier(reader)))


def record_owned_rehearsal(owner, present):
    directory = os.environ.get('TEMPO_DEVELOPMENT_BUDGET_DIRECTORY')
    token = os.environ.get('TEMPO_DEVELOPMENT_BUDGET_TOKEN')
    if not directory or not token:
        raise RuntimeError('Standalone proof requires scripts/development-budget.mjs')
    from urllib.parse import quote
    root = Path(directory)
    metadata = json.loads((root/'owner.json').read_text())
    if metadata['token'] != token: raise RuntimeError('Rehearsal budget owner changed')
    resource = root/'resources'/quote('rehearsal:'+owner,safe='')
    if present: resource.write_text(json.dumps({'token':token,'pid':os.getpid(),'owner':owner}))
    else: resource.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list',action='store_true')
    parser.add_argument('--quick',action='store_true')
    parser.add_argument('--owned-stack',action='store_true')
    parser.add_argument('--evidence',type=Path,default=ROOT/'test-results/study-priority-native')
    args = parser.parse_args()
    if args.list: print(json.dumps(CASES)); return
    args.evidence.mkdir(parents=True,exist_ok=True)
    if args.owned_stack:
        if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
            raise RuntimeError('The owning disposable runner is required')
        result = run_proof('postgresql://postgres@postgres:5432/tempo','redis://redis:6379/0',args.evidence,
            graph_rows=10_000 if args.quick else 1_400_000,receipt_rows=20_000 if args.quick else 415_000)
        (args.evidence/'native.json').write_text(json.dumps({'complete':True,'cases':result},indent=2)+'\n')
        print(json.dumps(result,indent=2)); return
    owner = 'tempo-study-priority-'+secrets.token_hex(8)
    password = secrets.token_urlsafe(32)
    names = [owner+'-postgres',owner+'-redis']
    report = {'owner':owner,'complete':False,'cleanup':False,'cases':{},'quick':args.quick,
              'source_sha256': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in [Path(__file__), * (ROOT/'backend/app').rglob('*.py')]},
              'revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()}
    attempted = []
    record_owned_rehearsal(owner,True)
    try:
        for name,image,port,memory,cpu in ((names[0],'postgres:18.6-trixie',5432,'512m','1'),
                                           (names[1],'redis:7-alpine',6379,'64m','0.1')):
            attempted.append(name)
            docker('run','--detach','--name',name,'--label','tempo.owner='+owner,'--label','tempo.disposable=true',
                   '--label','tempo.resource-class=development','--cpus',cpu,'--memory',memory,
                   '--publish',f'127.0.0.1::{port}','--env','POSTGRES_PASSWORD',image,
                   environment={**os.environ,'POSTGRES_PASSWORD':password})
        postgres_port = json.loads(docker('inspect',names[0]))[0]['NetworkSettings']['Ports']['5432/tcp'][0]['HostPort']
        redis_port = json.loads(docker('inspect',names[1]))[0]['NetworkSettings']['Ports']['6379/tcp'][0]['HostPort']
        parent_url = make_conninfo(host='127.0.0.1',port=postgres_port,user='postgres',password=password,dbname='postgres')
        deadline = time.monotonic()+30
        while True:
            try:
                with psycopg.connect(parent_url) as database:
                    database.execute("COMMENT ON DATABASE postgres IS 'tempo-disposable-postgres-test'")
                break
            except psycopg.OperationalError:
                assert time.monotonic()<deadline,'Owned PostgreSQL did not become ready'; time.sleep(0.1)
        report['cases'] = run_proof(parent_url,f'redis://127.0.0.1:{redis_port}/0',args.evidence,
            graph_rows=10_000 if args.quick else 1_400_000,receipt_rows=20_000 if args.quick else 415_000,
            postgres_container=names[0])
        report['complete'] = True
    finally:
        cleanup_errors=[]
        for name in reversed(attempted):
            try:
                details = json.loads(docker('inspect',name))[0]
                assert details['Config']['Labels']['tempo.owner'] == owner
                if details['State'].get('Paused'): docker('unpause',name)
                docker('rm','--force','--volumes',name)
            except Exception as error: cleanup_errors.append(str(error))
        if not cleanup_errors: record_owned_rehearsal(owner,False)
        report['cleanup'] = not cleanup_errors
        report['cleanup_errors'] = cleanup_errors
        (args.evidence/'native.json').write_text(json.dumps(report,indent=2)+'\n')
        if cleanup_errors: raise RuntimeError('Owned resource cleanup failed: '+str(cleanup_errors))
    print(json.dumps(report,indent=2))


if __name__ == '__main__': main()
