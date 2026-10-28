"""Bounded Study pickup with historical evidence and stale queue statistics."""
import json
from datetime import timedelta
import os
from pathlib import Path
import subprocess
import uuid

from test_product_game_lapse_postgres import owned_postgres, case, NOW
from test_study_encounter_intake_boundary import representative, state


def test_historical_evidence_ownership_pickup_commits_within_existing_budget_and_replays(case):
    image = os.environ.get("TEMPO_PRIORITY_WORKER_IMAGE",
        "sha256:836538aedba0e57668119f90b247cb92a1fcef2a012f93754aae84cdbb256123")
    root = Path(__file__).resolve().parents[2]
    identity = "tempo-488-ownership-" + uuid.uuid4().hex
    report = {"owner": identity, "image": image, "worker_cpus": 0.15, "worker_memory_mib": 128,
              "postgres_cpus": 0.4, "historical_evidence_rows": 5312, "cleanup_verified": False}
    # Preserve a recently analyzed, almost empty queue estimate, as on the
    # failing installation. No ANALYZE after this day's queue is populated.
    with case.transaction() as database:
        database.execute_native("ANALYZE daily_queue")
        database.execute_native("ANALYZE cards")
    with representative(case) as (capture, snapshot, command):
        with case.transaction() as database:
            database.execute_native("""INSERT INTO opening_evidence_attempts(attempt_id,manifest_id,
                presentation_snapshot_id,repertoire_id,card_id,card_revision,trained_color,
                context_json,manifest_json,started_at,study_timezone,queue_entry_id,state)
                SELECT %s||':old-evidence:'||n,'synthetic-history',p.id,%s,p.card_id,p.revision,'white',
                    json_build_object('synthetic', (SELECT string_agg(md5(n::text||':'||b::text),'')
                        FROM generate_series(1,16) b))::text,
                    json_build_object('synthetic', (SELECT string_agg(md5(n::text||':'||b::text),'')
                        FROM generate_series(17,46) b))::text,
                    %s,'UTC',CASE WHEN n=1 THEN NULL ELSE old_queue.id END,'complete'
                FROM generate_series(1,5312) n
                CROSS JOIN LATERAL (SELECT id,card_id,revision FROM opening_evidence_presentations
                    WHERE card_id=%s ORDER BY id LIMIT 1) p
                CROSS JOIN LATERAL (SELECT id FROM daily_queue WHERE card_id=%s AND queue_date<%s
                    ORDER BY id LIMIT 1) old_queue""",
                (case.card_id, command.decision.repertoire_id, NOW.isoformat(),
                 case.card_id+':1', case.card_id+':1', NOW.date().isoformat()))
            assert database.execute_native("SELECT COUNT(*) FROM opening_evidence_attempts").fetchone()[0] == 5312
            database.execute_native("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind,status) VALUES(%s,%s,0,'review','complete')",
                ((NOW-timedelta(days=1)).date().isoformat(),case.card_id+':0'))
            database.execute_native("UPDATE queue_attempt_origins SET issued_as_head=1 WHERE queue_entry_id=(SELECT MIN(id) FROM daily_queue WHERE card_id=%s AND queue_date<%s)",
                (case.card_id+':0', NOW.date().isoformat()))
            database.execute_native("UPDATE daily_queue SET position=712 WHERE card_id=%s AND queue_date=%s",
                (case.card_id+':573', NOW.date().isoformat()))
            database.execute_native("UPDATE daily_queue SET position=573 WHERE card_id=%s AND queue_date=%s",
                (case.card_id, NOW.date().isoformat()))
            before = state(database, case)
            assert len(before['reviews']) == len(before['receipts']) == 9 and len(before['protected']) == 26
            connection = database.raw.info
            assert connection.dbname.startswith('tempo_game_feedback_')
            options = dict(host='host.docker.internal', port=connection.port, dbname=connection.dbname,
                user=connection.user, password=connection.password, sslmode='disable', connect_timeout=3)
        postgres = options['dbname'].replace('_', '-') + '-postgres'
        metadata = json.loads(subprocess.run(['docker','inspect',postgres],capture_output=True,text=True,check=True).stdout)[0]
        assert metadata['Config']['Labels']['tempo.owner'] == options['dbname'].replace('_','-')
        subprocess.run(['docker','update','--cpus','0.4',postgres],capture_output=True,text=True,check=True)
        payload = dict(options=options,capture=capture,snapshot=snapshot.model_dump(mode='json'),
                       command=command.model_dump(mode='json'),now=NOW.isoformat())
        source = 'payload=' + repr(payload) + '\n' + r'''
import os,json,time,builtins
from datetime import datetime,timedelta
from contextlib import contextmanager
os.environ['TEMPO_DATABASE_READ_URL']='postgresql://synthetic-disposable-only'
import psycopg
from tempo_legacy_database.adapters.postgres_store import PostgresConnection,tempo_row_factory
from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
from app.contracts.product_v02.repertoire import RepertoireSnapshot
from app.study.interfaces.game_priority import GameEncounter
from app.product_composition.encounters.receiver import receive_captured_encounter
from app.product_composition.feedback_maintenance import KIND,execute_feedback_sweep
from app.services import durable_tasks
from tempo_legacy_database.adapters.durable_tasks import storage
command=GameEncounter.model_validate_json(json.dumps(payload['command']))
snapshot=RepertoireSnapshot.model_validate_json(json.dumps(payload['snapshot']))
now=datetime.fromisoformat(payload['now']);durable_tasks._now=lambda:now
times=[]
@contextmanager
def read():
 with psycopg.connect(**payload['options'],row_factory=tempo_row_factory) as raw:
  raw.execute("SET LOCAL transaction_timeout='250ms'")
  raw.execute('SET TRANSACTION READ ONLY')
  yield PostgresConnection(raw,read_only=True)
@contextmanager
def write():
 raw=psycopg.connect(**payload['options'],row_factory=tempo_row_factory)
 try:
  raw.execute("SELECT set_config('transaction_timeout','250ms',true),set_config('statement_timeout','250ms',true),set_config('lock_timeout','25ms',true)")
  started=time.perf_counter()
  yield PostgresConnection(raw)
  raw.commit();times.append(round((time.perf_counter()-started)*1000,2))
 except BaseException:raw.rollback();raise
 finally:raw.close()
with write() as database:
 accepted=receive_captured_encounter(database,capture=payload['capture'],snapshot=snapshot,command=command,clock=lambda:now)
 assert accepted.admission_status=='pending'
with write() as database:
 task=durable_tasks.enqueue_task_in_transaction(database,KIND,'ownership-regression',{'after':''})
 storage.claim_lease(database,('ownership-lease',(now+timedelta(days=2)).isoformat(),now.isoformat(),now.isoformat(),task['id'],task['generation']))
 claimed=dict(storage.task_by_id(database,(task['id'],)))
original_import=builtins.__import__
def no_games(name,*args,**kwargs):
 if name=='app.games' or name.startswith('app.games.'):raise AssertionError('Study pickup imported Games')
 return original_import(name,*args,**kwargs)
builtins.__import__=no_games
assert execute_feedback_sweep(claimed,read=read,write=write,capture=lambda *_:payload['capture'],clock=lambda:now) is False
with write() as database:
 replay=receive_captured_encounter(database,capture=payload['capture'],snapshot=snapshot,command=command,clock=lambda:now)
 assert replay.owner_receipt_id==accepted.owner_receipt_id and replay.admission_status=='available'
with read() as database:
 effect=PostgresGameFeedbackStore(database).get(command.key)
 assert effect.encounter==command and effect.status=='applied'
 assert database.execute_native('SELECT position FROM daily_queue WHERE card_id=%s AND queue_date=%s',(command.decision.presentation_id,now.date().isoformat())).fetchone()[0]==1
 assert database.execute_native('SELECT COUNT(*) FROM study_game_feedback WHERE card_id=%s',(command.decision.presentation_id,)).fetchone()[0]==1
print(json.dumps(dict(intake_ms=times[0],task_ms=times[1],refresh_ms=times[2],replay_ms=times[3],whole_transactions_committed=True)))
'''
        adapter = root/'database/tempo_legacy_database/adapters/queue_refresh/presentation.py'
        args = ['docker','run','-i','--rm','--pull=never','--name',identity,
            '--label','tempo.owner='+identity,'--label','tempo.disposable=true',
            '--cpus','0.15','--memory','128m','--memory-swap','128m','--pids-limit','64',
            '--entrypoint','python','--mount',f'type=bind,source={adapter},target=/app/tempo_legacy_database/adapters/queue_refresh/presentation.py,readonly',
            image,'-']
        try:
            result = subprocess.run(args,input=source,capture_output=True,text=True,timeout=90)
            report.update(exit_code=result.returncode,stdout=result.stdout,stderr=result.stderr)
            assert result.returncode == 0, result.stdout+'\n'+result.stderr
            report['timings'] = json.loads(result.stdout.strip().splitlines()[-1])
            with case.transaction() as database:
                assert state(database,case) == before
            print('OWNERSHIP_PICKUP',json.dumps(report['timings']))
        finally:
            found = subprocess.run(['docker','ps','-aq','--filter','name=^/'+identity+'$'],capture_output=True,text=True,check=True).stdout.strip()
            if found:
                current = json.loads(subprocess.run(['docker','inspect',identity],capture_output=True,text=True,check=True).stdout)[0]
                assert current['Config']['Labels']['tempo.owner'] == identity
                subprocess.run(['docker','rm','--force',identity],capture_output=True,text=True,check=True)
            assert not subprocess.run(['docker','ps','-aq','--filter','name=^/'+identity+'$'],capture_output=True,text=True,check=True).stdout.strip()
            report['cleanup_verified'] = True
            evidence = root/'test-results/priority-ownership'
            evidence.mkdir(parents=True,exist_ok=True)
            (evidence/(identity+'.json')).write_text(json.dumps(report,indent=2)+'\n')
