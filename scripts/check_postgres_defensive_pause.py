"""Default-off pause, mixed engine purposes, foreground admission and restart on disposable PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import threading
import uuid

import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from scripts.apply_postgres_migrations import apply_migrations
from app import database, main as application, postgres_store, settings_commands, threat_analysis_commands
from app.models import Settings
from app.services import background_activity, background_diagnostics, durable_tasks
from app.services.threat_detection import find_defensive_knight_forks
from app.services.threat_models import GameSnapshot, SourceLine
from app.services.threat_pipeline import _upsert_seed

ADMIN_DSN = 'postgresql://postgres@postgres:5432/postgres'


def verify_pause():
    with postgres_store.connection() as connection:
        connection.execute('INSERT INTO settings(id) VALUES(1)')
        assert connection.execute('SELECT defensive_analysis_enabled FROM settings WHERE id=1').fetchone()[0] == 0
        fen = '4k3/8/8/8/1n6/8/P7/R3K3 w Q - 0 1'
        game = GameSnapshot('lichess:pause-proof', 1, fen, ('a2a3',), 'white')
        seed = find_defensive_knight_forks(game, SourceLine('engine', 1, ('b4c2','e1d2','c2a1','d2c1')))[0]
        now = datetime.now(timezone.utc).isoformat()
        connection.execute("""INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,
            result,start_fen,moves_json,analysis_state,analysis_version)
            VALUES(?,'lichess','proof',?,'rapid',1,'white','0-1',?,'["a2a3"]','ready',1)""", (game.game_id, now, fen))
        _upsert_seed(connection, game, seed)
        request_id = connection.execute('SELECT id FROM threat_analysis_requests ORDER BY id LIMIT 1').fetchone()[0]
    with postgres_store.connection(background=True) as connection:
        assert threat_analysis_commands.claim_threat_analysis(connection, {}) == {'job': None}
    with postgres_store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM threat_analysis_requests WHERE attempts!=0 OR state!='queued'").fetchone()[0] == 0
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('pause-rep','Pause','proof',?)", (now,))
        connection.execute("""INSERT INTO repertoire_opportunities(id,repertoire_id,kind,fen_key,score,
            evidence_json,evidence_fingerprint,created_at,updated_at)
            VALUES('pause-opportunity','pause-rep','missing_response','position',1,'{}','fingerprint',?,?)""", (now,now))
        connection.execute("""INSERT INTO discovery_recommendation_requests(opportunity_id,request_id,source_game_id,
            source_ply,created_at,updated_at) VALUES('pause-opportunity',?,'lichess:pause-proof',0,?,?)""", (request_id,now,now))
    with postgres_store.connection(background=True) as connection:
        claimed = threat_analysis_commands.claim_threat_analysis(connection,{})['job']
    assert claimed['id'] == request_id
    assert application.defensive_engine_control(request_id,claimed['lease_id'])['search_allowed']
    assert not application.defensive_engine_control(request_id,'stale')['search_allowed']
    assert next(item for item in background_activity.list_activity()['items'] if item['id']==request_id)['title']=='Repertoire recommendation search'
    with postgres_store.connection() as connection:
        connection.execute("UPDATE repertoire_opportunities SET status='dismissed'")
    assert not application.defensive_engine_control(request_id,claimed['lease_id'])['search_allowed']
    with postgres_store.connection(background=True) as connection:
        assert threat_analysis_commands.release_threat_analysis(connection,{'request_id':request_id,'lease_id':claimed['lease_id']})['status']=='queued'
        assert threat_analysis_commands.release_threat_analysis(connection,{'request_id':request_id,'lease_id':claimed['lease_id']})['status']=='stale'
        assert connection.execute('SELECT attempts FROM threat_analysis_requests WHERE id=?',(request_id,)).fetchone()[0]==0
    queued=durable_tasks.enqueue_task('defensive_threat_scan','proof',{})
    assert durable_tasks.claim_task('defensive_threat_scan') is None
    with postgres_store.connection() as connection:
        values=Settings(defensive_analysis_enabled=True).model_dump()
        settings_commands.update_settings(connection,{'settings':values,'supplied_fields':['defensive_analysis_enabled']})
    claimed_slice=durable_tasks.claim_task('defensive_threat_scan')
    with postgres_store.connection() as connection:
        connection.execute('UPDATE settings SET defensive_analysis_enabled=0')
    started=threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        def delayed_slice():
            started.set()
            return durable_tasks.defer_paused_defensive_task(claimed_slice)
        with database.activity_gate.foreground():
            future=executor.submit(delayed_slice)
            assert started.wait(2)
            # Foreground reads remain available while the slice waits for admission.
            assert application.get_settings().defensive_analysis_enabled is False
        assert future.result(timeout=5)
    assert durable_tasks.defer_paused_defensive_task(claimed_slice)
    with postgres_store.connection() as connection:
        state=connection.execute('SELECT state,attempt_count FROM background_tasks WHERE id=?',(queued['id'],)).fetchone()
        assert tuple(state)==('queued',0), state
    assert durable_tasks.claim_task('defensive_threat_scan') is None
    snapshot = background_diagnostics.snapshot()
    assert snapshot.available, snapshot.model_dump()
    assert any(row.queue=='durable' and row.state=='paused' and row.count>0 for row in snapshot.queues)
    durable_tasks.enqueue_task('daily_queue','foreground-proof',{})
    assert durable_tasks.claim_task('daily_queue')['kind']=='daily_queue'
    postgres_store.close_pools()
    apply_migrations(os.environ['TEMPO_DATABASE_WRITE_URL'])
    assert application.get_settings().defensive_analysis_enabled is False
    with postgres_store.connection() as connection:
        connection.execute('UPDATE settings SET defensive_analysis_enabled=1')
        settings_commands.update_settings(connection,{'settings':Settings().model_dump(),'supplied_fields':[]})
    assert application.get_settings().defensive_analysis_enabled is True
    resumed=durable_tasks.claim_task('defensive_threat_scan')
    assert resumed['id']==queued['id']
    assert durable_tasks.complete_task(resumed['id'],resumed['generation'],resumed['lease_token'])
    assert not durable_tasks.complete_task(resumed['id'],resumed['generation'],resumed['lease_token'])
    print('PASS defensive_pause_postgres_restart_foreground_and_idempotent_resume')


def main():
    if os.getenv('TEMPO_TEST_INSTANCE')!='disposable':
        raise RuntimeError('Defensive pause proof requires the disposable test runner')
    database_name='tempo_defensive_pause_'+uuid.uuid4().hex
    dsn=f'postgresql://postgres@postgres:5432/{database_name}'
    with psycopg.connect(ADMIN_DSN,autocommit=True) as administrator:
        administrator.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database_name)))
    try:
        apply_migrations(dsn)
        os.environ['TEMPO_DATABASE_WRITE_URL']=dsn
        os.environ['TEMPO_DATABASE_READ_URL']=dsn
        os.environ.pop('TEMPO_REDIS_URL',None)
        os.environ.pop('TEMPO_FOREGROUND_ACTIVITY_URL',None)
        verify_pause()
    finally:
        postgres_store.close_pools()
        with psycopg.connect(ADMIN_DSN,autocommit=True) as administrator:
            administrator.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database_name)))


if __name__=='__main__':
    main()
