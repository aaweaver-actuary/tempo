"""Run retained graph cleanup, restart and history proof on disposable PostgreSQL."""
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(root), str(root / 'database'), str(root / 'backend')]


def main():
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Background proof requires the disposable test runner')
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    bind_database_infrastructure()
    from database.tests.check_postgres_graph_retention import run
    run()


if __name__ == '__main__' and len(sys.argv) == 1:
    main()


def stale_delivery_witness(phase, project):
    """Real broker deliveries retain retired identities while Study progresses."""
    import json
    import time
    from datetime import date, datetime, timezone
    import psycopg
    from psycopg.rows import dict_row
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    bind_database_infrastructure()
    from app.celery_app import celery_app
    from app.services import durable_tasks
    from tempo_legacy_database.adapters import postgres_store

    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable' or not project.startswith('tempo-pg-regressions-'):
        raise RuntimeError('Broker witness requires the owned disposable test project')
    if phase not in ('before', 'after'):
        raise ValueError('Unknown stale-delivery phase')
    dsn = 'postgresql://postgres@postgres:5432/tempo'
    os.environ['TEMPO_DATABASE_WRITE_URL'] = dsn
    os.environ['TEMPO_DATABASE_READ_URL'] = dsn
    postgres_store.close_pools()
    prefix = 'retired-witness-' + project
    fg, bg, task_id = prefix + '-foreground', prefix + '-background', prefix + '-task'
    retired_tables = ('next_opponent_accounts', 'next_opponent_snapshots', 'defense_attempts', 'endgame_attempts',
                      'game_analysis_publications', 'game_analysis_position_reports', 'game_analysis_position_errors',
                      'game_analysis_jobs', 'engine_scheduling_state')

    def snapshot():
        with psycopg.connect(dsn, row_factory=dict_row) as db:
            receipt = db.execute('SELECT row_to_json(r)::text AS row FROM operation_receipts r WHERE operation_id=%s', (fg,)).fetchone()
            absent = db.execute('SELECT count(*) AS n FROM operation_receipts WHERE operation_id=%s', (bg,)).fetchone()['n']
            task = db.execute('SELECT row_to_json(t)::text AS row FROM background_tasks t WHERE id=%s', (task_id,)).fetchone()
            missing = {name: db.execute('SELECT to_regclass(%s) IS NULL AS missing', ('public.' + name,)).fetchone()['missing'] for name in retired_tables}
        assert receipt and task and absent == 0 and all(missing.values()), (receipt, task, absent, missing)
        return {'receipt': receipt['row'], 'task': task['row'], 'retired_tables_absent': missing}

    if phase == 'before':
        now = datetime.now(timezone.utc).isoformat()
        with psycopg.connect(dsn) as db:
            db.execute("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state) VALUES(%s,'endgames.attempt.create','synthetic-original','pending')", (fg,))
            db.execute("""INSERT INTO background_tasks(id,kind,deduplication_key,generation,priority,state,phase,payload_json,
                attempt_count,max_attempts,next_attempt_at,lease_token,lease_expires_at,created_at,updated_at)
                VALUES(%s,'defensive_threat_scan',%s,7,100,'leased','original','{}',2,5,%s,'original-lease',%s,%s,%s)""",
                (task_id, task_id, now, '2099-01-01T00:00:00+00:00', now, now))
        initial = snapshot()
    else:
        initial = snapshot()

    tasks = [
        ('app.tasks.execute_foreground_command', [fg, 'endgames.attempt.create', {'synthetic': True}], 'foreground'),
        ('app.tasks.execute_background_command', [bg, 'games.analysis.position.report', {'synthetic': True}], 'background'),
        ('app.tasks.execute_background_slice', [{'id': task_id, 'kind': 'defensive_threat_scan', 'generation': 7,
                                                 'lease_token': 'original-lease'}], 'background'),
    ]
    deliveries = []
    for index, (name, args, queue) in enumerate(tasks):
        transport_id = f'{prefix}-{phase}-{index}'
        result = celery_app.send_task(name, args=args, queue=queue, task_id=transport_id)
        value = result.get(timeout=45, interval=0.1, propagate=True)
        assert result.state == 'SUCCESS' and value in (None, False), (name, result.state, value)
        deliveries.append({'task': name, 'transport_id': transport_id, 'state': result.state, 'result': value})

    if phase == 'before':
        queue = durable_tasks.enqueue_task('daily_queue', 'current', {'queue_date': date.today().isoformat()}, priority=10, foreground=True)
        deadline = time.monotonic() + 90
        while True:
            with psycopg.connect(dsn, row_factory=dict_row) as db:
                row = db.execute('SELECT id,generation,state FROM background_tasks WHERE id=%s', (queue['id'],)).fetchone()
            if row and row['state'] == 'complete' and row['generation'] == queue['generation']:
                break
            assert time.monotonic() < deadline, f'retained daily queue did not complete: {row}'
            celery_app.send_task('app.tasks.progress_study_queue', queue='foreground')
            time.sleep(0.25)
    else:
        with psycopg.connect(dsn, row_factory=dict_row) as db:
            row = db.execute("SELECT id,generation,state FROM background_tasks WHERE kind='daily_queue' AND deduplication_key='current'").fetchone()
        assert row and row['state'] == 'complete', f'retained queue did not stay complete after restart: {row}'
    final = snapshot()
    assert final == initial, 'retired delivery changed the original durable state or recreated retired tables'
    print('STALE_DELIVERY_WITNESS ' + json.dumps({'phase': phase, 'snapshot': final, 'queue': dict(row), 'deliveries': deliveries}, sort_keys=True))


if __name__ == '__main__' and len(sys.argv) == 4 and sys.argv[1] == '--stale-delivery':
    stale_delivery_witness(sys.argv[2], sys.argv[3])
