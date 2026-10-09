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
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from scripts.apply_postgres_migrations import apply_migrations
from app import activity_commands, database, main as application, postgres_store, settings_commands, threat_analysis_commands
from app.models import Settings
from app.services import background_activity, background_diagnostics, durable_tasks
from app.services.threat_detection import find_defensive_knight_forks
from app.services.threat_models import GameSnapshot, SourceLine
from app.services.threat_pipeline import _upsert_seed

ADMIN_DSN = os.environ.get('TEMPO_PAUSE_ADMIN_DSN', 'postgresql://postgres@postgres:5432/postgres')


def verify_activity_pause_provenance(request_id, task_id, unrelated_task_id):
    """Actual PostgreSQL controls preserve individual pauses across global changes and reconnect."""
    def item(source, work_id):
        return next(row for row in background_activity.list_activity()['items']
                    if row['source'] == source and row['id'] == work_id)

    def control(source, work_id, action):
        with postgres_store.connection() as connection:
            return activity_commands.control_activity(connection, {'source': source, 'id': work_id, 'action': action})

    for source, work_id in [('durable', task_id), ('threat_analysis', request_id)]:
        with postgres_store.connection() as connection:
            controls_before = [tuple(row) for row in connection.execute('SELECT * FROM background_activity ORDER BY source,work_id')]
        projected = item(source, work_id)
        assert (projected['state'], projected['paused'], projected['paused_by_settings']) == ('paused', True, True)
        assert item(source, work_id) == projected
        with postgres_store.connection() as connection:
            assert [tuple(row) for row in connection.execute('SELECT * FROM background_activity ORDER BY source,work_id')] == controls_before
        assert control(source, work_id, 'pause') == {'ok': True}
        with postgres_store.connection() as connection:
            individual_before = tuple(connection.execute('SELECT * FROM background_activity WHERE source=? AND work_id=?',
                                                        (source, work_id)).fetchone())
        try:
            control(source, work_id, 'resume')
            raise AssertionError('Globally blocked Resume falsely succeeded')
        except HTTPException as error:
            assert error.status_code == 409 and 'Defensive analysis' in error.detail and 'Settings' in error.detail
        postgres_store.close_pools()
        with postgres_store.connection() as connection:
            assert tuple(connection.execute('SELECT * FROM background_activity WHERE source=? AND work_id=?',
                                            (source, work_id)).fetchone()) == individual_before
            assert connection.execute('SELECT defensive_analysis_enabled FROM settings WHERE id=1').fetchone()[0] == 0
            connection.execute('UPDATE settings SET defensive_analysis_enabled=1')
        projected = item(source, work_id)
        assert (projected['state'], projected['paused'], projected['paused_by_settings']) == ('paused', True, False)
        assert control(source, work_id, 'resume') == {'ok': True}
        projected = item(source, work_id)
        assert projected['paused'] is False and projected['paused_by_settings'] is False
        with postgres_store.connection() as connection:
            assert connection.execute('SELECT paused FROM background_activity WHERE source=? AND work_id=?',
                                      (source, work_id)).fetchone()[0] == 0
            connection.execute('UPDATE settings SET defensive_analysis_enabled=0')
        assert control(source, work_id, 'prioritize') == {'ok': True}
        assert item(source, work_id)['promoted'] is True
        assert control(source, work_id, 'normal') == {'ok': True}
    assert control('durable', task_id, 'pause') == {'ok': True}
    with postgres_store.connection() as connection:
        connection.execute("UPDATE background_tasks SET state='failed',last_error='retry fixture' WHERE id=?", (task_id,))
    with postgres_store.connection() as connection:
        assert activity_commands.retry_failed_task(connection, {'task_id': task_id})['state'] == 'queued'
    projected = item('durable', task_id)
    assert projected['paused'] is True and projected['paused_by_settings'] is True
    with postgres_store.connection() as connection:
        assert connection.execute("SELECT paused FROM background_activity WHERE source='durable' AND work_id=?",
                                  (task_id,)).fetchone()[0] == 0
    with postgres_store.connection() as connection:
        connection.execute("UPDATE repertoire_opportunities SET status='active'")
    assert control('threat_analysis', request_id, 'pause') == {'ok': True}
    assert item('threat_analysis', request_id)['paused_by_settings'] is False
    assert control('threat_analysis', request_id, 'resume') == {'ok': True}
    assert item('threat_analysis', request_id)['paused'] is False
    for action, paused in [('pause', True), ('resume', False)]:
        assert control('durable', unrelated_task_id, action) == {'ok': True}
        projected = item('durable', unrelated_task_id)
        assert projected['paused'] is paused and projected['paused_by_settings'] is False
    with postgres_store.connection() as connection:
        assert connection.execute('SELECT defensive_analysis_enabled FROM settings WHERE id=1').fetchone()[0] == 0
        connection.execute("UPDATE repertoire_opportunities SET status='dismissed'")
    print('PASS defensive_activity_global_and_individual_pause_provenance_stale_resume_and_restart')


def verify_cancellation_retry_allowance(request_id):
    """Actual committed PostgreSQL callbacks refund one cancellation, never a genuine failure."""
    def claim():
        with postgres_store.connection(background=True) as connection:
            job = threat_analysis_commands.claim_threat_analysis(connection, {})['job']
        assert job and job['id'] == request_id
        return job['lease_id']

    def release(lease_id):
        with postgres_store.connection(background=True) as connection:
            return threat_analysis_commands.release_threat_analysis(connection, {
                'request_id': request_id, 'lease_id': lease_id})

    def request_state():
        with postgres_store.connection(background=True) as connection:
            return dict(connection.execute('SELECT state,attempts,lease_id,lease_expires_at,report_json '
                                           'FROM threat_analysis_requests WHERE id=?', (request_id,)).fetchone())

    with postgres_store.connection() as connection:
        connection.execute("UPDATE threat_analysis_requests SET state='complete' WHERE id!=?", (request_id,))
        connection.execute('UPDATE threat_analysis_requests SET report_json=? WHERE id=?',
                           ('{"retained":"completed evidence"}', request_id))
        durable_before = [tuple(row) for row in connection.execute(
            'SELECT id,state,attempt_count,generation FROM background_tasks ORDER BY id')]
        reviews_before = connection.execute('SELECT COUNT(*) FROM reviews').fetchone()[0]
    for _ in range(4):
        cancelled = claim()
        with postgres_store.connection() as connection:
            connection.execute('UPDATE settings SET defensive_analysis_enabled=0')
        with postgres_store.connection() as connection:
            connection.execute('UPDATE settings SET defensive_analysis_enabled=1')
        assert release(cancelled) == {'status': 'queued'}
        assert request_state() == {'state': 'queued', 'attempts': 0, 'lease_id': None,
                                   'lease_expires_at': None, 'report_json': '{"retained":"completed evidence"}'}
        assert release(cancelled) == {'status': 'stale'}
    newer = claim()
    before_stale = request_state()
    assert release(cancelled) == {'status': 'stale'}
    assert request_state() == before_stale
    assert release(newer) == {'status': 'queued'}
    with postgres_store.connection() as connection:
        connection.execute('UPDATE settings SET defensive_analysis_enabled=0')
        connection.execute("UPDATE repertoire_opportunities SET status='active'")
    shared = claim()
    assert release(shared) == {'status': 'queued'}
    assert request_state()['attempts'] == 0
    for failure_count in range(1, 4):
        failed_lease = claim()
        with postgres_store.connection(background=True) as connection:
            assert threat_analysis_commands.fail_threat_analysis(connection, {
                'request_id': request_id, 'lease_id': failed_lease, 'error': 'genuine failure'}) == {
                    'status': 'failed' if failure_count == 3 else 'retrying'}
        if failure_count < 3:
            assert release(claim()) == {'status': 'queued'}
        assert request_state()['attempts'] == failure_count
    with postgres_store.connection(background=True) as connection:
        assert threat_analysis_commands.claim_threat_analysis(connection, {}) == {'job': None}
        assert [tuple(row) for row in connection.execute(
            'SELECT id,state,attempt_count,generation FROM background_tasks ORDER BY id')] == durable_before
        assert connection.execute('SELECT COUNT(*) FROM reviews').fetchone()[0] == reviews_before
    print('PASS defensive_cancel_release_after_resume_preserves_failures_and_fences_replay')


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
    from app.services.activity_health import monitor_one_pipeline
    for _ in range(250):
        if not monitor_one_pipeline(evidence={'available':False,'foreground':False}):
            break
    else:
        raise AssertionError('Bounded pause diagnostics bootstrap failed to complete')
    snapshot = background_diagnostics.snapshot()
    assert snapshot.available, snapshot.model_dump()
    assert any(row.queue=='durable' and row.state=='paused' and row.count>0 for row in snapshot.queues)
    assert snapshot.query_duration_seconds < background_diagnostics.QUERY_BUDGET_SECONDS
    print('PASS defensive_diagnostics_canonical_freshness_classification_within_unchanged_query_budget')
    unrelated = durable_tasks.enqueue_task('daily_queue','foreground-proof',{})
    assert durable_tasks.claim_task('daily_queue')['kind']=='daily_queue'
    verify_activity_pause_provenance(request_id, queued['id'], unrelated['id'])
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
    verify_cancellation_retry_allowance(request_id)
    print('PASS defensive_pause_postgres_restart_foreground_and_idempotent_resume')


def main():
    if os.getenv('TEMPO_TEST_INSTANCE')!='disposable':
        raise RuntimeError('Defensive pause proof requires the disposable test runner')
    database_name='tempo_defensive_pause_'+uuid.uuid4().hex
    dsn=psycopg.conninfo.make_conninfo(ADMIN_DSN,dbname=database_name)
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
