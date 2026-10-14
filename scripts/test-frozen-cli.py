#!/usr/bin/env python3
"""Disposable end-to-end proof for frozen Maia plan/apply/verify/rollback."""
from __future__ import annotations
import json, os, secrets, shutil, socket, subprocess, tempfile, time, urllib.request
from pathlib import Path
import psycopg

ROOT=Path(__file__).resolve().parents[1]

def run(args, env, *, capture=False, check=True):
    result=subprocess.run(args,cwd=ROOT,env=env,text=True,stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.STDOUT if capture else None,timeout=900)
    if check and result.returncode:
        raise RuntimeError(f"{args[0]} exited {result.returncode}: {result.stdout[-4000:] if capture else ''}")
    return result.stdout.strip() if capture else result

def main():
    token=secrets.token_hex(5); project=f"tempo-frozen-cli-{os.getpid()}-{token}"
    secrets_dir=Path(tempfile.mkdtemp(prefix='.tempo-frozen-cli-secrets-',dir=ROOT));secrets_dir.chmod(0o700)
    admin=secrets.token_urlsafe(24); reader=secrets.token_urlsafe(24); writer=secrets.token_urlsafe(24)
    files={"admin_password":admin+"\n","reader_password":reader+"\n","writer_password":writer+"\n",
           "admin_pgpass":f"postgres:5432:*:postgres:{admin}\n",
           "reader_pgpass":f"postgres:5432:tempo:tempo_reader:{reader}\n",
           "writer_pgpass":f"postgres:5432:tempo:tempo_writer:{writer}\n"}
    for name,value in files.items():
        p=secrets_dir/name;p.write_text(value);p.chmod(0o600)
    overlay=secrets_dir/'compose.yml'
    overlay.write_text('services:\n  postgres:\n    ports: ["127.0.0.1::5432"]\n  redis:\n    ports: ["127.0.0.1::6379"]\n  api:\n    ports: ["127.0.0.1::8000"]\n')
    env={k:v for k,v in os.environ.items() if not (k.startswith('TEMPO_') or k in {'DATABASE_URL','REDIS_URL','PGHOST','PGPORT','PGDATABASE','PGUSER','PGPASSWORD','PGPASSFILE','COMPOSE_FILE','COMPOSE_PROJECT_NAME'})}
    env.update({"TEMPO_PG_TEST_SECRETS":str(secrets_dir),"TEMPO_TEST_INSTANCE":"disposable",
                "TEMPO_POSTGRES_ADMIN_PASSWORD_FILE":str(secrets_dir/'admin_password'),
                "TEMPO_POSTGRES_ADMIN_PGPASS_FILE":str(secrets_dir/'admin_pgpass'),
                "TEMPO_POSTGRES_READER_PGPASS_FILE":str(secrets_dir/'reader_pgpass'),
                "TEMPO_POSTGRES_WRITER_PGPASS_FILE":str(secrets_dir/'writer_pgpass'),
                "TEMPO_REDIS_URL":"redis://redis:6379/0"})
    compose=['docker','compose','-p',project,'-f','docker-compose.postgres.test.yml','-f',str(overlay)]
    out=secrets_dir/'run';out.mkdir()
    try:
        assert not run(['docker','ps','-aq','--filter',f'label=com.docker.compose.project={project}'],env,capture=True)
        run([*compose,'up','--build','-d','postgres','redis','schema','api','foreground-worker','background-worker','background-scheduler'],env)
        postgres_port=int(run([*compose,'port','postgres','5432'],env,capture=True).rsplit(':',1)[1])
        api_port=int(run([*compose,'port','api','8000'],env,capture=True).rsplit(':',1)[1])
        redis_port=int(run([*compose,'port','redis','6379'],env,capture=True).rsplit(':',1)[1])
        env['TEMPO_REDIS_URL']=f'redis://127.0.0.1:{redis_port}/0'
        host=f'127.0.0.1:{postgres_port}'
        env['PGPASSFILE']=str(secrets_dir/'host.pgpass')
        Path(env['PGPASSFILE']).write_text(f'{host}:tempo:postgres:{admin}\n{host}:tempo:tempo_reader:{reader}\n{host}:tempo:tempo_writer:{writer}\n')
        Path(env['PGPASSFILE']).chmod(0o600)
        dsn_admin=f'postgresql://postgres:{admin}@127.0.0.1:{postgres_port}/tempo'
        dsn_reader=f'postgresql://tempo_reader:{reader}@127.0.0.1:{postgres_port}/tempo'
        dsn_writer=f'postgresql://tempo_writer:{writer}@127.0.0.1:{postgres_port}/tempo'
        env['TEMPO_DATABASE_READ_URL']=dsn_reader;env['TEMPO_DATABASE_WRITE_URL']=dsn_writer
        base=f'http://127.0.0.1:{api_port}'
        for _ in range(180):
            try:
                with urllib.request.urlopen(base+'/api/health',timeout=2) as response:
                    health=json.load(response)
                if health.get('storage')=='postgresql' and health.get('test_instance'):break
            except Exception:time.sleep(1)
        else:raise RuntimeError('Disposable API did not become healthy')
        # Verify the application has distinct read-only and writer identities.
        with psycopg.connect(dsn_reader) as conn:
            assert conn.execute("SELECT current_user,current_setting('transaction_read_only')").fetchone()==('tempo_reader','on')
        with psycopg.connect(dsn_writer) as conn:
            assert conn.execute('SELECT current_user').fetchone()[0]=='tempo_writer'
        # A one-scenario real Maia route keeps this proof bounded while exercising
        # the same graph, plan, API, workers, and PostgreSQL contracts as production.
        repertoire='frozen-cli-fixture'; line='frozen-cli-line'; card='frozen-cli-card'
        moves=['e2e4','e7e5','g1f3','b8c6','f1b5']
        with psycopg.connect(dsn_admin) as conn:
            conn.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,%s,%s,%s)",(repertoire,'CLI proof','fixture','2026-10-14'))
            conn.execute('INSERT INTO settings(id) VALUES(1) ON CONFLICT(id) DO NOTHING')
            conn.execute('UPDATE settings SET study_only_mode=1,new_cards_per_day=2,coverage_maia_elo=1500 WHERE id=1')
            conn.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES(%s,1,'ready','2026-10-14')",(repertoire,))
            conn.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(%s,%s,'CLI proof','white',%s,%s,'2026-10-14')",(line,repertoire,'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',json.dumps(moves)))
            conn.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color) VALUES(%s,%s,'prefix',%s,%s,'new','2026-10-14','opening','white')",(card,repertoire,'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',json.dumps(moves)))
            conn.execute('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)',(repertoire,card))
            conn.execute("INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json) VALUES(%s,1,%s,0,%s,NULL,'root',%s,%s,'white','prefix',0,0,'[]')",(repertoire,line,card,'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',json.dumps(moves)))
        config=out/'config.json';config.write_text(json.dumps({'version':1,'scenarios':[{'name':'CLI proof','repertoire_id':repertoire,'color':'white','prefix':['e4'],'weight':1.0}], 'expected_database':'tempo','expected_schema':'public','maia_elo_override':1500,'study_timezone':'America/New_York'}))
        cli=str(ROOT/'scripts/frozen-maia-order.py');plan_dir=out/'plan'
        cmd=[str(ROOT/'backend/.venv/bin/python'),cli,'plan','--config',str(config),'--allow-unscored-tail','--output',str(plan_dir),'--foreground-url',base+'/api/system/foreground-requests-active']
        plan_result=json.loads(run(cmd,env,capture=True).splitlines()[-1]);assert plan_result['activation_safe'] and plan_result['scored']>=1,plan_result
        target=plan_result['target'];plan_path=str(plan_dir/'plan.json')
        apply=[str(ROOT/'backend/.venv/bin/python'),cli,'apply','--plan',plan_path,'--target',target,'--reader-url',base]
        first_output=run(apply,env,capture=True);assert 'Override applied' in first_output,first_output
        second=json.loads(run(apply,env,capture=True).splitlines()[-1]);assert second.get('run_id')==plan_result['run_id'],second
        for _ in range(60):
            with urllib.request.urlopen(base+'/api/queue/today',timeout=10) as response: json.load(response)
            verification=json.loads(run([str(ROOT/'backend/.venv/bin/python'),cli,'verify','--plan',plan_path,'--target',target,'--reader-url',base],env,capture=True).splitlines()[-1])
            if verification['state']=='active':break
            time.sleep(1)
        else:raise RuntimeError(f'Queue refresh/reader confirmation did not complete: {verification}')
        # Seed one eligible queue candidate in the settled disposable projection;
        # the API must serve and issue it through its real durable head path.
        with psycopg.connect(dsn_admin) as conn:
            conn.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind,admission_repertoire_id) VALUES('2026-10-14',%s,0,'new',%s) ON CONFLICT(queue_date,card_id,cycle) DO NOTHING",(card,repertoire))
        with urllib.request.urlopen(base+'/api/queue/today',timeout=10) as response:queue=json.load(response)
        assert any(item['id']==card for item in queue['cards']),queue
        # Queue fetch invokes the real head-issuance path; assert the durable receipt.
        with psycopg.connect(dsn_admin) as conn:
            assert conn.execute('SELECT COUNT(*) FROM queue_attempt_origins WHERE card_id=%s AND issued_as_head=1',(card,)).fetchone()[0]>=1
        rolled=json.loads(run([str(ROOT/'backend/.venv/bin/python'),cli,'rollback','--target',target,'--run-id',plan_result['run_id']],env,capture=True).splitlines()[-1])
        assert rolled.get('study_preserved') is True,rolled
        check=json.loads(run([str(ROOT/'backend/.venv/bin/python'),cli,'verify','--plan',plan_path,'--target',target,'--reader-url',base],env,capture=True).splitlines()[-1])
        assert check['state']=='inactive',check
        print(json.dumps({'result':'PASS','run_id':plan_result['run_id'],'target':target,'proofs':['real Maia inference','read-only reader and writer roles','disposable API and workers','bounded queue refresh and actual queue issuance','repeat apply','verify','rollback preserves study'],'seconds':round(time.monotonic()-started,2)}))
    finally:
        down=subprocess.run([*compose,'down','-v','--remove-orphans'],cwd=ROOT,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
        if down.returncode: print(down.stdout[-4000:])
        shutil.rmtree(secrets_dir,ignore_errors=True)

if __name__=='__main__':
    started=time.monotonic()
    main()
