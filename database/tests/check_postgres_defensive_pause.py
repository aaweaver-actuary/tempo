"""Disposable retained task selection, foreground admission and restart proof."""
import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(root), str(root / 'database'), str(root / 'backend')]
import psycopg
from psycopg import sql
from database.tooling.apply_postgres_migrations import apply_migrations
from tempo_legacy_database.adapters import postgres_store
from app.services.activity_gate import activity_gate
from app.services import durable_tasks

ADMIN_DSN = 'postgresql://postgres@postgres:5432/postgres'


def verify_retained_tasks():
    with postgres_store.connection() as connection:
        connection.execute("INSERT INTO settings(id,study_only_mode) VALUES(1,0)")
    # Separate logical kinds exercise bounded selection without provider I/O.
    queue = durable_tasks.enqueue_task('daily_queue', 'proof-queue', {'queue_date': '2026-01-01'})
    game = durable_tasks.enqueue_task('game_sync_window', 'proof-provider', {'window_id': 'synthetic-window'})
    claimed = durable_tasks.claim_task(allowed_kinds=('game_sync_window',))
    assert claimed['id'] == game['id'] and claimed['kind'] == 'game_sync_window'
    assert claimed['payload'] == {'window_id': 'synthetic-window'}
    started = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        def defer():
            started.set()
            return durable_tasks.defer_task_for_foreground(claimed)
        with activity_gate.foreground():
            future = executor.submit(defer)
            assert started.wait(2)
            with postgres_store.connection() as connection:
                assert connection.execute('SELECT 1').fetchone()[0] == 1
        assert future.result(timeout=5)
    with postgres_store.connection() as connection:
        row = connection.execute('SELECT state,attempt_count,generation FROM background_tasks WHERE id=?', (game['id'],)).fetchone()
        assert tuple(row) == ('retrying', 0, claimed['generation'])
        connection.execute("UPDATE background_tasks SET next_attempt_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", game["id"]))
    postgres_store.close_pools()
    resumed = durable_tasks.claim_task(allowed_kinds=('game_sync_window',))
    assert resumed['id'] == game['id'] and resumed['generation'] == claimed['generation']
    assert not durable_tasks.complete_task(claimed['id'], claimed['generation'], claimed['lease_token'], kind=claimed['kind'])
    assert durable_tasks.complete_task(resumed['id'], resumed['generation'], resumed['lease_token'], kind=resumed['kind'])
    assert not durable_tasks.complete_task(resumed['id'], resumed['generation'], resumed['lease_token'], kind=resumed['kind'])
    queue_claim = durable_tasks.claim_task(allowed_kinds=('daily_queue',))
    assert queue_claim['id'] == queue['id']
    assert durable_tasks.complete_task(queue_claim['id'], queue_claim['generation'], queue_claim['lease_token'], kind=queue_claim['kind'])
    print('PASS retained_provider_and_queue_selection_foreground_defer_restart_and_stale_receipt')


def main():
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Task proof requires the disposable PostgreSQL test runner')
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    bind_database_infrastructure()
    name = 'tempo_task_proof_' + uuid.uuid4().hex
    dsn = f'postgresql://postgres@postgres:5432/{name}'
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    try:
        apply_migrations(dsn, defense_zero=True)
        os.environ['TEMPO_DATABASE_WRITE_URL'] = dsn
        os.environ['TEMPO_DATABASE_READ_URL'] = dsn
        os.environ.pop('TEMPO_REDIS_URL', None)
        os.environ.pop('TEMPO_FOREGROUND_ACTIVITY_URL', None)
        verify_retained_tasks()
    finally:
        postgres_store.close_pools()
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


if __name__ == '__main__':
    main()
